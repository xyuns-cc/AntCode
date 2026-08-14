"""
ControlService gRPC 服务实现 (P1c + P3)

负责 Worker 生命周期 (Register/Deregister)、租约 (Lease)、任务取消
(CancelTask)、配置更新 (UpdateConfig) 以及反向控制通道
(WatchControl / AckControl)。

P3 LeaseStore 已接管租约状态机:

- ``Register`` 沿用旧 ``GatewayServiceImpl.Register`` 的 API Key 验证逻辑，
  只返回租约策略；``Lease`` 是唯一创建或续租的入口。
- ``Lease`` 通过 ``LeaseStore.grant(worker_id, current_lease_id, metrics)``
  发租 / 续租,返回真 lease_id + expires_at_ms。``request.metrics`` 顺便
  写到 ``heartbeat:{worker_id}`` Hash,保留运维 dashboard 可见。
- ``CancelTask`` / ``UpdateConfig`` 把控制指令 xadd 到对应
  ``control:{worker_id}`` Stream（payload 与 control_plane helpers 对齐），
  由 ``WatchControl`` 消费推送。
- ``WatchControl`` 是 server-streaming：从
  ``control:{worker_id}`` + ``control:global`` 两个 Stream
  ``XREADGROUP`` 后封装成 ``ControlEvent`` yield 给 Worker。
- ``AckControl`` XACK 对应 stream 的 message。``event_id`` 编码格式
  ``{stream_key}|{redis_msg_id}``，与服务端 yield 时使用的 ``event_id`` 一致。
  P1-16：格式化 event_id 无进程内映射，多 gateway 副本 / 重启后依然可 ACK。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

import grpc
from antcode_contracts import control_pb2
from antcode_contracts.control_pb2_grpc import ControlServiceServicer
from antcode_contracts.wire_contract import WorkerWireContractError
from antcode_core.application.services.lease_service import (
    LeaseConflictError,
    LeaseIneligibleError,
    LeaseRevokedError,
    wire_lease_policy,
)
from antcode_core.application.services.workers.worker_lease_issuance import (
    WorkerLeaseGrantRequest,
    WorkerLeaseRejected,
    grant_authorized_worker_lease,
)
from antcode_core.infrastructure.redis import (
    build_cancel_control_payload,
    build_config_update_control_payload,
    control_global_group,
    control_global_stream,
    control_group,
    control_stream,
    get_redis_client,
    trim_acknowledged_stream,
)
from loguru import logger

from antcode_gateway.auth import require_authenticated_worker
from antcode_gateway.handlers import LeaseHandler
from antcode_gateway.security_audit import SecurityAuditor
from antcode_gateway.services.control_ack_settlement import settle_control_ack as _settle_control_ack
from antcode_gateway.services.control_event_builders import build_control_event
from antcode_gateway.services.control_stream_ownership import (
    ControlPendingEntry,
    ControlPendingOwnerError,
    ack_owned_control_entry,
    control_consumer_name,
    recover_stale_control_entries,
    stream_recovered_control_entries,
)
from antcode_gateway.services.lease_heartbeat import (
    decode_lease_heartbeat,
    granted_lease_response,
    persist_legacy_heartbeat,
    revoked_lease_response,
)
from antcode_gateway.services.registration_gate import audit_registration_rejection, registration_validation_error
from antcode_gateway.services.runtime_control_expiry import prepare_runtime_control_delivery
from antcode_gateway.services.runtime_control_recovery import recover_committed_runtime_result

if TYPE_CHECKING:  # pragma: no cover - typing only
    from antcode_core.application.services.lease_service import LeaseStore
    from antcode_core.application.services.workers.worker_lease_authority import WorkerLeaseAuthorizer

# WatchControl 内部轮询间隔（block 毫秒）
CONTROL_POLL_BLOCK_MS = 1_000

# P1-#8: control:{worker_id} stream 的近似最大长度,避免无界增长。
CONTROL_STREAM_MAXLEN = 1_000


async def _abort(context: grpc.aio.ServicerContext, code: grpc.StatusCode, details: str) -> NoReturn:
    """终止 RPC。``abort`` 必然抛异常；它一旦返回就说明 gRPC 契约被破坏，绝不能伪造响应顶上。"""
    await context.abort(code, details)
    raise RuntimeError("gRPC context.abort returned unexpectedly")


@dataclass(frozen=True)
class _ControlChannel:
    stream_key: str
    group: str


@dataclass(frozen=True)
class _ControlWatchSession:
    context: grpc.aio.ServicerContext
    worker_id: str
    lease_id: str
    consumer: str
    channels: tuple[_ControlChannel, ...]


def _stream_messages(result):
    for raw_stream, messages in result or []:
        stream_key = raw_stream.decode() if isinstance(raw_stream, bytes) else str(raw_stream)
        for raw_message_id, data in messages:
            message_id = raw_message_id.decode() if isinstance(raw_message_id, bytes) else str(raw_message_id)
            yield stream_key, message_id, data


def _parse_control_event_id(event_id: str, worker_id: str) -> tuple[str, str]:
    if not event_id or "|" not in event_id:
        raise ValueError("AckControl event_id 格式无效")
    stream_key, message_id = event_id.split("|", 1)
    if not stream_key or not message_id:
        raise ValueError("AckControl event_id 缺失字段")
    allowed_streams = {control_stream(worker_id), control_global_stream()}
    if stream_key not in allowed_streams:
        raise PermissionError("control stream does not belong to authenticated worker")
    return stream_key, message_id


def _control_group_for_stream(stream_key: str, worker_id: str) -> str:
    if stream_key == control_stream(worker_id):
        return control_group()
    if stream_key == control_global_stream():
        return control_global_group(worker_id)
    raise PermissionError("control stream does not belong to authenticated worker")


async def _trim_settled_control(
    redis,
    stream_key: str,
    message_id: str,
    *,
    group: str,
) -> None:
    try:
        selected_group = None if stream_key == control_global_stream() else group
        await trim_acknowledged_stream(redis, stream_key, selected_group)
    except Exception:
        logger.exception(
            "AckControl 后裁剪失败: stream={} msg_id={}",
            stream_key,
            message_id,
        )


class GatewayControlService(ControlServiceServicer):
    """ControlService Gateway 端实现。

    P3：可选注入 ``LeaseStore``，接管 Lease 状态机。注入后：
    - ``Register`` 成功时返回 lease 策略，不修改 lease 状态。
    - ``Lease`` 走 ``LeaseStore.grant`` 真发租 / 续租（含 lease_id 一致性）。
    - ``Deregister`` 调 ``LeaseStore.revoke`` 主动撤销。
    未注入时 Register / Lease / Deregister 一律 UNAVAILABLE，没有占位实现。
    """

    def __init__(
        self,
        lease_handler: LeaseHandler | None = None,
        lease_store: LeaseStore | None = None,
        *,
        lease_authorizer: WorkerLeaseAuthorizer | None = None,
    ):
        self._lease_handler = lease_handler or LeaseHandler()
        self._lease_store = lease_store
        self._lease_authorizer = lease_authorizer
        # 默认 no-op；生产由 main 在装配阶段 bind_security_auditor 接上真实 Stream。
        self._security_auditor = SecurityAuditor()
        self._control_group_lock = asyncio.Lock()
        self._initialized_control_groups: set[tuple[str, str]] = set()
        # P1-16: event_id 用自解释的 f"{stream_key}|{msg_id}",AckControl 直接反解析。
        # 进程内映射会在多副本 / 重启后丢失,使 AckControl 恒 received=False 而无限重投;
        # stream_key 只含 worker 自知的 worker_id,不构成新的信息泄露。
        if lease_store is not None:
            logger.info(
                "ControlService 已初始化（LeaseStore 已注入: ttl_ms={}, renew_after_ms={}）",
                lease_store.policy.ttl_ms,
                lease_store.policy.renew_after_ms,
            )
        else:
            logger.info("ControlService 已初始化（LeaseStore 未注入：Register / Lease 一律 UNAVAILABLE）")

    def bind_security_auditor(self, auditor: SecurityAuditor) -> None:
        """接入安全审计 Stream（由 main 装配）。Register 免认证，爆破只有这里看得见。"""
        self._security_auditor = auditor

    async def Register(
        self,
        request: control_pb2.RegisterRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.RegisterResponse:
        worker_id = request.worker_id
        api_key = request.api_key
        logger.info(f"收到 ControlService.Register 请求: worker_id={worker_id}")

        if not api_key:
            return await self._reject_registration(context, worker_id, "缺少 API Key")

        if error_msg := await registration_validation_error(api_key, worker_id):
            return await self._reject_registration(context, worker_id, error_msg)

        if self._lease_store is None:
            logger.error("Register 拒绝: LeaseStore 未就绪")
            return control_pb2.RegisterResponse(success=False, error="lease service unavailable")
        logger.info(f"Worker 注册成功: worker_id={worker_id}")
        return control_pb2.RegisterResponse(
            success=True,
            worker_id=worker_id,
            lease_ttl_ms=self._lease_store.policy.ttl_ms,
            lease_renew_after_ms=self._lease_store.policy.renew_after_ms,
            runtime_control_results_v1=True,
        )

    async def _reject_registration(
        self, context: grpc.aio.ServicerContext, worker_id: str, error: str
    ) -> control_pb2.RegisterResponse:
        """记录并返回一次 Register 拒绝（含 audit:security 事件）。"""
        peer = str(context.peer() or "")
        await audit_registration_rejection(self._security_auditor, worker_id, peer=peer, error=error)
        return control_pb2.RegisterResponse(success=False, error=error)

    async def GetCapabilities(
        self,
        request: control_pb2.CapabilitiesRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.CapabilitiesResponse:
        """Return protocol capabilities without mutating Worker lease state."""
        await require_authenticated_worker(context, request.worker_id)
        return control_pb2.CapabilitiesResponse(
            runtime_control_results_v1=True,
            runtime_control_lease_fencing_v1=True,
            runtime_control_deadline_v1=True,
            artifact_transfer_v1=True,
            runtime_control_authoritative_clock_v1=True,
            worker_capabilities_v1=True,
        )

    async def _require_current_lease(
        self,
        context: grpc.aio.ServicerContext,
        worker_id: str,
        lease_id: str,
    ) -> None:
        if self._lease_store is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "lease service unavailable")
            return
        if not lease_id or not await self._lease_store.is_current(worker_id, lease_id):
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "worker lease is not current")

    async def Deregister(
        self,
        request: control_pb2.DeregisterRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.DeregisterResponse:
        worker_id = await require_authenticated_worker(context, request.worker_id)
        reason = request.reason or "explicit"
        logger.info(f"Worker Deregister: worker_id={worker_id}, reason={reason}")
        if self._lease_store is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "lease service unavailable")
            return control_pb2.DeregisterResponse(success=False, error="lease service unavailable")
        try:
            revoked = await self._lease_store.revoke(
                worker_id,
                reason=f"deregister:{reason}",
                lease_id=request.lease_id,
            )
        except Exception as exc:
            logger.exception("Deregister 撤销 lease 失败: {}", exc)
            await context.abort(grpc.StatusCode.UNAVAILABLE, "lease revoke failed")
            return control_pb2.DeregisterResponse(success=False, error="lease revoke failed")
        if not revoked:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "worker lease is not current")
            return control_pb2.DeregisterResponse(success=False, error="worker lease is not current")
        # 同时清理过渡期心跳 Hash（运维 dashboard 兼容）。
        try:
            redis = await get_redis_client()
            if redis is not None:
                from antcode_core.infrastructure.redis import worker_heartbeat_key

                await redis.delete(worker_heartbeat_key(worker_id))
        except Exception as exc:
            logger.warning(f"Deregister 清理 heartbeat 失败: {exc}")
        return control_pb2.DeregisterResponse(success=True)

    # =========================================================================
    # Lease (heartbeat 替身 - 真实 lease 状态机 TODO P3)
    # =========================================================================

    async def Lease(
        self,
        request: control_pb2.LeaseRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.LeaseResponse:
        """Issue or renew only after authoritative lifecycle checks."""
        worker_id = await require_authenticated_worker(context, request.worker_id)
        if not worker_id:
            # LeaseStore 未注入时也必须下发权威时序，走共享工厂（这里曾是第 10 份硬写的 TTL/renew 副本）。
            return revoked_lease_response(wire_lease_policy(), "worker_id 为空")
        if self._lease_store is None:
            await _abort(context, grpc.StatusCode.UNAVAILABLE, "lease service unavailable")
        if self._lease_authorizer is None:
            await _abort(context, grpc.StatusCode.UNAVAILABLE, "lease authority unavailable")
        try:
            heartbeat = decode_lease_heartbeat(request, worker_id)
        except ValueError:
            logger.warning("Lease metrics 或 capabilities 非法: worker_id={}", worker_id)
            await _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "invalid worker metrics or capabilities")
        try:
            lease = await grant_authorized_worker_lease(
                self._lease_store,
                self._lease_authorizer,
                WorkerLeaseGrantRequest(
                    worker_id=worker_id,
                    current_lease_id=request.current_lease_id or "",
                    metrics=heartbeat.metrics,
                    capabilities=heartbeat.capabilities,
                ),
            )
        except WorkerWireContractError as exc:
            # 线协议错配必须比 fencing 更响：运维看到的是"Worker 在线但零产出"，
            # 只有把版本要求写进日志与 revoke_reason 才排得出来。
            logger.error("Lease 拒发（Worker 线协议版本不兼容）: worker_id={} reason={}", worker_id, exc)
            return revoked_lease_response(self._lease_store.policy, str(exc))
        except (LeaseConflictError, LeaseIneligibleError, LeaseRevokedError, WorkerLeaseRejected) as exc:
            logger.warning("Lease 被 fencing: worker_id={} lease_id={}", worker_id, request.current_lease_id)
            return revoked_lease_response(self._lease_store.policy, str(exc))
        except Exception as exc:
            logger.exception(f"Lease grant 失败: worker_id={worker_id}, exc={exc}")
            await _abort(context, grpc.StatusCode.UNAVAILABLE, "lease grant failed")
        await persist_legacy_heartbeat(self._lease_handler, heartbeat.heartbeat)
        return granted_lease_response(lease, self._lease_store.policy)

    # =========================================================================
    # CancelTask / UpdateConfig - 推到 control stream
    # =========================================================================

    async def CancelTask(
        self,
        request: control_pb2.CancelTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.CancelTaskResponse:
        worker_id = await require_authenticated_worker(context, request.worker_id)
        # P2-#19: 协议违规 (参数缺失 / redis 不可用) 走 gRPC error。
        if not worker_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "worker_id 不能为空")
            return control_pb2.CancelTaskResponse(success=False, error="worker_id 不能为空")

        payload = build_cancel_control_payload(
            run_id=request.run_id or request.task_id,
            reason=request.reason,
            task_id=request.task_id or request.run_id,
        )
        stream_payload: dict[str | bytes | int | float, str | bytes | int | float] = {
            key: value for key, value in payload.items()
        }
        try:
            redis = await get_redis_client()
            if redis is None:
                await context.abort(grpc.StatusCode.UNAVAILABLE, "redis unavailable")
                return control_pb2.CancelTaskResponse(success=False, error="redis unavailable")
            await redis.xadd(
                control_stream(worker_id),
                stream_payload,
            )
            logger.info(f"已下发任务取消到 control:{worker_id}: task_id={request.task_id}")
            return control_pb2.CancelTaskResponse(success=True)
        except grpc.aio.AbortError:
            raise
        except Exception as exc:
            logger.exception(f"CancelTask 写 Stream 失败: {exc}")
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
            return control_pb2.CancelTaskResponse(success=False, error=str(exc))

    async def UpdateConfig(
        self,
        request: control_pb2.UpdateConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.UpdateConfigResponse:
        worker_id = await require_authenticated_worker(context, request.worker_id)
        # P2-#19: 协议违规 (参数缺失 / redis 不可用) 走 gRPC error。
        if not worker_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "worker_id 不能为空")
            return control_pb2.UpdateConfigResponse(success=False, error="worker_id 不能为空")

        config = dict(request.config) if request.config else {}
        payload = build_config_update_control_payload(config)
        stream_payload: dict[str | bytes | int | float, str | bytes | int | float] = {
            key: value for key, value in payload.items()
        }
        try:
            redis = await get_redis_client()
            if redis is None:
                await context.abort(grpc.StatusCode.UNAVAILABLE, "redis unavailable")
                return control_pb2.UpdateConfigResponse(success=False, error="redis unavailable")
            await redis.xadd(
                control_stream(worker_id),
                stream_payload,
            )
            logger.info(f"已下发配置更新到 control:{worker_id}")
            return control_pb2.UpdateConfigResponse(success=True)
        except grpc.aio.AbortError:
            raise
        except Exception as exc:
            logger.exception(f"UpdateConfig 写 Stream 失败: {exc}")
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
            return control_pb2.UpdateConfigResponse(success=False, error=str(exc))

    # =========================================================================
    # WatchControl (server-streaming) + AckControl
    # =========================================================================

    async def WatchControl(
        self,
        request: control_pb2.WatchControlRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[control_pb2.ControlEvent]:
        """长连接：消费 control:{worker_id} + control:global，封装成 ControlEvent。

        从旧 ``GatewayServiceImpl._control_poller`` 抽取并改造为 server-streaming
        模式，避免 worker 端的拉取协程开销。
        """
        worker_id = await require_authenticated_worker(context, request.worker_id)
        if not worker_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "worker_id 不能为空")
            return
        await self._require_current_lease(context, worker_id, request.lease_id)
        redis = await get_redis_client()
        if redis is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "redis unavailable")
            return

        channels = (
            _ControlChannel(control_stream(worker_id), control_group()),
            _ControlChannel(control_global_stream(), control_global_group(worker_id)),
        )
        for channel in channels:
            await self._ensure_control_group(redis, channel.stream_key, channel.group)
        consumer = control_consumer_name(worker_id, request.lease_id)
        session = _ControlWatchSession(
            context=context,
            worker_id=worker_id,
            lease_id=request.lease_id,
            consumer=consumer,
            channels=channels,
        )
        try:
            claimed = await recover_stale_control_entries(
                redis,
                channels=channels,
                worker_id=worker_id,
                lease_id=request.lease_id,
            )
        except LeaseConflictError as exc:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return
        await self._require_current_lease(context, worker_id, request.lease_id)
        await context.send_initial_metadata(())
        if claimed:
            logger.info(
                "WatchControl 已接管旧代际 PEL: worker_id={} consumer={} claimed={}",
                worker_id,
                consumer,
                len(claimed),
            )
        logger.info(f"WatchControl 已建立: worker_id={worker_id}")
        try:
            async for event in self._replay_pending_control(redis, session):
                yield event
            async for event in self._stream_new_control(redis, session):
                yield event
        except LeaseConflictError as exc:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return
        except asyncio.CancelledError:
            logger.info(f"WatchControl 被取消: worker_id={worker_id}")
            raise
        finally:
            logger.info(f"WatchControl 已断开: worker_id={worker_id}")

    async def _replay_pending_control(
        self,
        redis,
        session: _ControlWatchSession,
    ) -> AsyncIterator[control_pb2.ControlEvent]:
        replayed = 0
        for channel in session.channels:
            cursor = "0-0"
            while True:
                # 租约 fencing 统一收敛在 _decode_control_event（yield 前逐条校验），
                # 此处不再重复检查，避免每条消息 3 次 Redis 往返。
                try:
                    result = await self._read_control_group(
                        redis,
                        session=session,
                        channel=channel,
                        cursor=cursor,
                        count=CONTROL_STREAM_MAXLEN,
                        block=0,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise RuntimeError(f"WatchControl PEL 排空失败: worker={session.worker_id}") from exc
                messages = list(_stream_messages(result))
                if not messages:
                    break
                for source_stream, message_id, data in messages:
                    event = await self._decode_control_event(
                        redis,
                        session,
                        stream_key=source_stream,
                        message_id=message_id,
                        data=data,
                    )
                    if event is not None:
                        replayed += 1
                        yield event
                if len(messages) < CONTROL_STREAM_MAXLEN:
                    break
                cursor = messages[-1][1]
        if replayed:
            logger.info(f"WatchControl PEL 排空: worker_id={session.worker_id} replayed={replayed}")

    async def _stream_new_control(
        self,
        redis,
        session: _ControlWatchSession,
    ) -> AsyncIterator[control_pb2.ControlEvent]:
        block_ms = max(1, CONTROL_POLL_BLOCK_MS // len(session.channels))
        while not session.context.cancelled():
            async for event in stream_recovered_control_entries(redis, session, self._decode_control_event):
                yield event
            # 每轮一次的廉价存活检查（逐条 fencing 在 _decode_control_event 内）。
            await self._require_current_lease(session.context, session.worker_id, session.lease_id)
            for channel in session.channels:
                try:
                    result = await self._read_control_group(
                        redis,
                        session=session,
                        channel=channel,
                        cursor=">",
                        count=1,
                        block=block_ms,
                    )
                except asyncio.CancelledError:
                    # C5：与 _replay_pending_control 一致重抛取消，让外层 WatchControl 的
                    # CancelledError 分支正确触发，别把取消伪装成干净 EOF。
                    raise
                except Exception as exc:
                    raise RuntimeError("WatchControl xreadgroup 失败") from exc
                for stream_key, message_id, data in _stream_messages(result):
                    event = await self._decode_control_event(
                        redis,
                        session,
                        stream_key=stream_key,
                        message_id=message_id,
                        data=data,
                    )
                    if event is not None:
                        yield event

    async def _decode_control_event(
        self,
        redis,
        session: _ControlWatchSession,
        *,
        stream_key: str,
        message_id: str,
        data: dict,
    ) -> control_pb2.ControlEvent | None:
        # 唯一的 per-message 租约 fencing 点（replay 与新消息都经过，勿在上游重复检查）。
        await self._require_current_lease(session.context, session.worker_id, session.lease_id)
        try:
            event = build_control_event(stream_key=stream_key, msg_id=message_id, data=data)
        except Exception:
            logger.exception(
                "control event 构建失败，按毒消息丢弃: stream={} msg_id={}",
                stream_key,
                message_id,
            )
            event = None
        if event is not None:
            recovered = await self._settle_recovered_control(
                redis,
                session,
                stream_key=stream_key,
                message_id=message_id,
                data=data,
            )
            if recovered:
                return None
            return await self._prepare_runtime_control_delivery(
                redis,
                session,
                stream_key=stream_key,
                message_id=message_id,
                event=event,
            )
        logger.warning(f"丢弃无法解析的 control event(已 ACK): stream={stream_key} msg_id={message_id}")
        group = _control_group_for_stream(stream_key, session.worker_id)
        try:
            await ack_owned_control_entry(
                redis,
                ControlPendingEntry(
                    session.worker_id,
                    session.lease_id,
                    stream_key,
                    group,
                    message_id,
                    session.consumer,
                ),
            )
        except Exception:
            logger.exception(
                "毒 control event ACK 失败: stream={} msg_id={}",
                stream_key,
                message_id,
            )
            raise
        return None

    async def _settle_recovered_control(
        self,
        redis,
        session: _ControlWatchSession,
        *,
        stream_key: str,
        message_id: str,
        data: dict,
    ) -> bool:
        recovered = await recover_committed_runtime_result(
            redis,
            event_id=f"{stream_key}|{message_id}",
            worker_id=session.worker_id,
            message_id=message_id,
            raw_event=data,
        )
        if not recovered:
            return False
        group = _control_group_for_stream(stream_key, session.worker_id)
        await ack_owned_control_entry(
            redis,
            ControlPendingEntry(
                session.worker_id,
                session.lease_id,
                stream_key,
                group,
                message_id,
                session.consumer,
            ),
        )
        await _trim_settled_control(redis, stream_key, message_id, group=group)
        logger.info(
            "恢复已提交的 runtime control: worker={} stream={} msg_id={}",
            session.worker_id,
            stream_key,
            message_id,
        )
        return True

    async def _prepare_runtime_control_delivery(
        self,
        redis,
        session: _ControlWatchSession,
        *,
        stream_key: str,
        message_id: str,
        event: control_pb2.ControlEvent,
    ) -> control_pb2.ControlEvent | None:
        return await prepare_runtime_control_delivery(
            redis,
            worker_id=session.worker_id,
            stream_key=stream_key,
            message_id=message_id,
            event=event,
            group=_control_group_for_stream(stream_key, session.worker_id),
            consumer_name=session.consumer,
            lease_id=session.lease_id,
            trim=_trim_settled_control,
        )

    async def AckControl(
        self,
        request: control_pb2.AckControlRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.AckControlResponse:
        worker_id = await require_authenticated_worker(context, request.worker_id)
        await self._require_current_lease(context, worker_id, request.lease_id)
        event_id = request.event_id
        try:
            stream_key, msg_id = _parse_control_event_id(event_id, worker_id)
            redis = await get_redis_client()
            if redis is None:
                raise RuntimeError("Redis unavailable")
            group = _control_group_for_stream(stream_key, worker_id)
            received = await _settle_control_ack(
                redis,
                event_id=event_id,
                stream_key=stream_key,
                message_id=msg_id,
                worker_id=worker_id,
                group=group,
                request=request,
            )
            if received:
                await _trim_settled_control(redis, stream_key, msg_id, group=group)
            if not received:
                logger.warning(f"AckControl 未命中: stream={stream_key} msg_id={msg_id}")
            if not request.success and request.error:
                logger.warning(f"Worker 上报控制事件执行失败: event_id={event_id} error={request.error}")
            return control_pb2.AckControlResponse(received=received)
        except PermissionError as exc:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
            return control_pb2.AckControlResponse(received=False)
        except LeaseConflictError as exc:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return control_pb2.AckControlResponse(received=False)
        except ControlPendingOwnerError as exc:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            return control_pb2.AckControlResponse(received=False)
        except ValueError as exc:
            logger.warning("AckControl 请求无效: worker={} event={} error={}", worker_id, event_id, exc)
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            return control_pb2.AckControlResponse(received=False)
        except Exception as exc:
            logger.exception(f"AckControl 异常: {exc}")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "control settlement unavailable")
            return control_pb2.AckControlResponse(received=False)

    # =========================================================================
    # 内部工具
    # =========================================================================

    async def _read_control_group(
        self,
        redis,
        *,
        session: _ControlWatchSession,
        channel: _ControlChannel,
        cursor: str,
        count: int,
        block: int,
    ):
        try:
            return await redis.xreadgroup(
                groupname=channel.group,
                consumername=session.consumer,
                streams={channel.stream_key: cursor},
                count=count,
                block=block,
            )
        except Exception as exc:
            if "NOGROUP" not in str(exc).upper():
                raise
            self._initialized_control_groups.discard((channel.stream_key, channel.group))
            await self._ensure_control_group(redis, channel.stream_key, channel.group)
            return await redis.xreadgroup(
                groupname=channel.group,
                consumername=session.consumer,
                streams={channel.stream_key: cursor},
                count=count,
                block=block,
            )

    async def _ensure_control_group(self, redis, stream_key: str, group: str) -> None:
        key = (stream_key, group)
        if key in self._initialized_control_groups:
            return

        async with self._control_group_lock:
            if key in self._initialized_control_groups:
                return
            try:
                await redis.xgroup_create(stream_key, group, id="0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    logger.exception(f"创建 control 消费者组失败: {exc}")
                    raise
            self._initialized_control_groups.add(key)
