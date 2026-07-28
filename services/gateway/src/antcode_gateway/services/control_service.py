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
- 若构造时未注入 LeaseStore(例如 Redis 不可用),退化为 P1c 阶段的
  ``lease-{worker_id}-{ts}`` 占位实现,保留兼容。
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
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import grpc
from antcode_contracts import control_pb2
from antcode_contracts.common_pb2 import Timestamp
from antcode_contracts.control_pb2_grpc import ControlServiceServicer
from antcode_core.application.services.lease_service import (
    LeaseConflictError,
    LeaseRevokedError,
)
from antcode_core.infrastructure.redis import (
    build_cancel_control_payload,
    build_config_update_control_payload,
    control_global_group,
    control_global_stream,
    control_group,
    control_stream,
    decode_stream_payload,
    get_redis_client,
    trim_acknowledged_stream,
)
from loguru import logger

from antcode_gateway.auth import require_authenticated_worker
from antcode_gateway.handlers import LeaseHandler
from antcode_gateway.services.runtime_control_expiry import (
    build_runtime_control_event,
    settle_expired_runtime_control,
)
from antcode_gateway.services.runtime_control_recovery import recover_committed_runtime_result
from antcode_gateway.services.runtime_control_result import (
    ControlEventGoneError,
    is_committed_runtime_result,
    publish_runtime_control_result,
    require_pending_owner,
    validate_control_ack,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from antcode_core.application.services.lease_service import LeaseStore
    from antcode_core.domain.models import Worker

# 兜底常量 —— 仅在未注入 LeaseStore 时使用（保持旧调用方兼容）。
# 真正的 TTL / renew_after 由 ``LeaseStore.policy`` 决定。
DEFAULT_LEASE_TTL_MS = 30_000
DEFAULT_LEASE_RENEW_AFTER_MS = 10_000

# WatchControl 内部轮询间隔（block 毫秒）
CONTROL_POLL_BLOCK_MS = 1_000

# P1-#8: control:{worker_id} stream 的近似最大长度,避免无界增长。
CONTROL_STREAM_MAXLEN = 1_000


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


async def _settle_control_ack(
    redis,
    *,
    event_id: str,
    stream_key: str,
    message_id: str,
    worker_id: str,
    group: str,
    request: control_pb2.AckControlRequest,
) -> bool:
    is_runtime = await is_committed_runtime_result(
        redis,
        event_id=event_id,
        worker_id=worker_id,
        request=request,
    )
    if not is_runtime:
        try:
            decoded, is_runtime = await validate_control_ack(
                redis,
                stream_key=stream_key,
                message_id=message_id,
                request=request,
            )
            await require_pending_owner(
                redis,
                stream_key=stream_key,
                group=group,
                message_id=message_id,
                worker_id=worker_id,
            )
        except ControlEventGoneError as exc:
            # at-least-once 重试撞上已结算/已裁剪的事件：幂等返回，不算协议错误。
            logger.info(
                "AckControl 幂等重放（事件已结算）: worker={} stream={} msg_id={} detail={}",
                worker_id,
                stream_key,
                message_id,
                exc,
            )
            return False
        if is_runtime:
            await publish_runtime_control_result(
                redis,
                event_id=event_id,
                worker_id=worker_id,
                message_id=message_id,
                decoded=decoded,
                request=request,
            )
    acked = await redis.xack(stream_key, group, message_id)
    return is_runtime or int(acked or 0) > 0


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
    未注入时退化为 P1c 的占位实现，保留旧测试调用方式可用。
    """

    def __init__(
        self,
        lease_handler: LeaseHandler | None = None,
        lease_store: LeaseStore | None = None,
    ):
        self._lease_handler = lease_handler or LeaseHandler()
        self._lease_store = lease_store
        self._control_group_lock = asyncio.Lock()
        self._initialized_control_groups: set[tuple[str, str]] = set()
        # P1-16: event_id 使用可自解释格式 f"{stream_key}|{msg_id}",AckControl
        # 直接反解析。原方案的进程内 _event_id_map 在多 gateway 副本 / 单副本重启
        # 后会丢失映射,导致 AckControl 恒返回 received=False,control 事件被
        # 无限重投。stream_key 本身是 control:{worker_id} / control:global,
        # worker 侧本就知道自己的 worker_id,不构成新的信息泄露。
        if lease_store is not None:
            logger.info(
                "ControlService 已初始化（LeaseStore 已注入: ttl_ms={}, renew_after_ms={}）",
                lease_store.policy.ttl_ms,
                lease_store.policy.renew_after_ms,
            )
        else:
            logger.info("ControlService 已初始化（LeaseStore 未注入，使用占位 Lease）")

    # =========================================================================
    # Register / Deregister
    # =========================================================================

    async def Register(
        self,
        request: control_pb2.RegisterRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.RegisterResponse:
        worker_id = request.worker_id
        api_key = request.api_key
        logger.info(f"收到 ControlService.Register 请求: worker_id={worker_id}")

        if not api_key:
            return control_pb2.RegisterResponse(success=False, error="缺少 API Key")

        is_valid, error_msg, _worker = await self._verify_registration(api_key=api_key, worker_id=worker_id)
        if not is_valid:
            logger.warning(f"Register 验证失败: worker_id={worker_id}, error={error_msg}")
            return control_pb2.RegisterResponse(success=False, error=error_msg)

        if self._lease_store is None:
            logger.error("Register 拒绝: LeaseStore 未就绪")
            return control_pb2.RegisterResponse(
                success=False,
                error="lease service unavailable",
            )
        logger.info(f"Worker 注册成功: worker_id={worker_id}")
        return control_pb2.RegisterResponse(
            success=True,
            worker_id=worker_id,
            lease_ttl_ms=self._lease_store.policy.ttl_ms,
            lease_renew_after_ms=self._lease_store.policy.renew_after_ms,
            runtime_control_results_v1=True,
        )

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

    async def _verify_registration(
        self,
        api_key: str,
        worker_id: str,
    ) -> tuple[bool, str, Worker | None]:
        """与旧 GatewayServiceImpl 行为一致的注册校验。"""
        try:
            from antcode_core.common.security.api_key import verify_api_key
            from antcode_core.domain.models import Worker

            if not await verify_api_key(api_key, worker_id or None):
                return False, "无效的 API Key", None
            worker = await Worker.filter(public_id=worker_id).first()
            if worker is None:
                return False, "Worker 不存在", None
            if worker_id and worker.public_id and worker_id != worker.public_id:
                return False, "Worker ID 不匹配", None
            return True, "", worker
        except ImportError:
            logger.exception("antcode_core.domain.models 不可用，无法验证注册 API Key")
            return False, "Worker/API Key 验证服务不可用", None
        except Exception as exc:
            logger.exception(f"验证注册请求异常: {exc}")
            return False, f"验证失败: {exc}", None

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
        """发租或续租：P3 走 LeaseStore，未注入时退回占位。"""
        worker_id = await require_authenticated_worker(context, request.worker_id)
        if not worker_id:
            return control_pb2.LeaseResponse(
                lease_id="",
                expires_at_ms=0,
                renew_after_ms=DEFAULT_LEASE_RENEW_AFTER_MS,
                revoked=True,
                revoke_reason="worker_id 为空",
            )
        if self._lease_store is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "lease service unavailable")
            return control_pb2.LeaseResponse(revoked=True)
        metrics_dict, lease_data = self._lease_request_metrics(request, worker_id)
        try:
            lease = await self._lease_store.grant(
                worker_id,
                current_lease_id=request.current_lease_id or "",
                metrics=metrics_dict,
            )
        except (LeaseConflictError, LeaseRevokedError) as exc:
            logger.warning("Lease 被 fencing: worker_id={} lease_id={}", worker_id, request.current_lease_id)
            return control_pb2.LeaseResponse(
                lease_id="",
                expires_at_ms=0,
                renew_after_ms=self._lease_store.policy.renew_after_ms,
                revoked=True,
                revoke_reason=str(exc),
            )
        except Exception as exc:
            logger.exception(f"Lease grant 失败: worker_id={worker_id}, exc={exc}")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "lease grant failed")
            return control_pb2.LeaseResponse(revoked=True)
        await self._persist_lease_heartbeat(lease_data)
        return control_pb2.LeaseResponse(
            lease_id=lease.lease_id,
            expires_at_ms=lease.expires_at_ms,
            renew_after_ms=self._lease_store.policy.renew_after_ms,
            revoked=False,
        )

    @staticmethod
    def _lease_request_metrics(request: control_pb2.LeaseRequest, worker_id: str) -> tuple[dict | None, Any]:
        if not request.HasField("metrics"):
            return None, None
        from antcode_gateway.handlers.heartbeat import LeaseData

        metrics = request.metrics
        values = {
            "cpu": metrics.cpu,
            "memory": metrics.memory,
            "disk": metrics.disk,
            "running_tasks": metrics.running_tasks,
            "max_concurrent_tasks": metrics.max_concurrent_tasks,
        }
        lease_data = LeaseData(
            worker_id=worker_id,
            status="online",
            cpu=metrics.cpu,
            memory=metrics.memory,
            disk=metrics.disk,
            running_tasks=metrics.running_tasks,
            max_concurrent_tasks=metrics.max_concurrent_tasks,
        )
        return values, lease_data

    async def _persist_lease_heartbeat(self, lease_data: Any) -> None:
        if lease_data is None:
            return
        try:
            await self._lease_handler.handle(lease_data)
        except Exception as exc:
            logger.warning(f"Lease 写 Redis 心跳 Hash 失败: {exc}")

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
        await context.send_initial_metadata(())
        session = _ControlWatchSession(
            context=context,
            worker_id=worker_id,
            lease_id=request.lease_id,
            consumer=worker_id,
            channels=channels,
        )
        logger.info(f"WatchControl 已建立: worker_id={worker_id}")
        try:
            async for event in self._replay_pending_control(redis, session):
                yield event
            async for event in self._stream_new_control(redis, session):
                yield event
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
            # 每轮一次的廉价存活检查：租约失效时尽快中断空转长轮询。
            # 逐条消息的 load-bearing fencing 在 _decode_control_event 内完成。
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
                    # C5：与 _replay_pending_control 一致，重新抛出取消而非吞掉，
                    # 让外层 WatchControl 的 CancelledError 分支正确触发，保持
                    # async 取消语义（不被伪装成干净 EOF）。
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
        # 唯一的 per-message 租约 fencing 点：读取之后、投递给 Worker 之前。
        # replay / 新消息两条路径都经过这里，勿在上游循环重复检查。
        await self._require_current_lease(session.context, session.worker_id, session.lease_id)
        try:
            event = self._build_control_event(stream_key=stream_key, msg_id=message_id, data=data)
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
            expired = await self._settle_expired_runtime_control(
                redis,
                session,
                stream_key=stream_key,
                message_id=message_id,
                event=event,
            )
            if expired:
                return None
            return event
        logger.warning(f"丢弃无法解析的 control event(已 ACK): stream={stream_key} msg_id={message_id}")
        group = _control_group_for_stream(stream_key, session.worker_id)
        try:
            await redis.xack(stream_key, group, message_id)
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
        await redis.xack(stream_key, group, message_id)
        await _trim_settled_control(redis, stream_key, message_id, group=group)
        logger.info(
            "恢复已提交的 runtime control: worker={} stream={} msg_id={}",
            session.worker_id,
            stream_key,
            message_id,
        )
        return True

    async def _settle_expired_runtime_control(
        self,
        redis,
        session: _ControlWatchSession,
        *,
        stream_key: str,
        message_id: str,
        event: control_pb2.ControlEvent,
    ) -> bool:
        return await settle_expired_runtime_control(
            redis,
            worker_id=session.worker_id,
            stream_key=stream_key,
            message_id=message_id,
            event=event,
            group=_control_group_for_stream(stream_key, session.worker_id),
            trim=_trim_settled_control,
        )

    def _build_control_event(
        self,
        stream_key: str,
        msg_id: str,
        *,
        data: dict,
    ) -> control_pb2.ControlEvent | None:
        """Decode one Redis control entry and dispatch to its typed builder."""
        try:
            decoded = decode_stream_payload(data)
        except Exception as exc:
            logger.exception(f"control stream payload 解码失败: {exc}")
            return None
        event_id = f"{stream_key}|{msg_id}"
        control_type = decoded.get("control_type", "")
        builder = self._control_event_builders().get(control_type)
        if builder is not None:
            return builder(event_id=event_id, stream_key=stream_key, msg_id=msg_id, decoded=decoded)
        logger.warning(f"未识别的 control_type: stream={stream_key} msg_id={msg_id} control_type={control_type}")
        return None

    def _control_event_builders(self) -> dict[str, Any]:
        return {
            "cancel": self._build_task_cancel_event,
            "kill": self._build_task_cancel_event,
            "config_update": self._build_config_update_event,
            "runtime_manage": self._build_runtime_control_event,
            "ping": self._build_ping_event,
        }

    def _build_task_cancel_event(self, *, event_id: str, decoded: dict, **_kwargs) -> control_pb2.ControlEvent:
        return control_pb2.ControlEvent(
            event_id=event_id,
            task_cancel=control_pb2.TaskCancel(
                task_id=str(decoded.get("task_id", "")),
                run_id=str(decoded.get("run_id", "")),
                reason=str(decoded.get("reason", "")),
            ),
        )

    def _build_config_update_event(self, *, event_id: str, decoded: dict, **_kwargs) -> control_pb2.ControlEvent:
        config = decoded.get("config") or {}
        if not isinstance(config, dict):
            config = {}
        return control_pb2.ControlEvent(
            event_id=event_id,
            config_update=control_pb2.ConfigUpdate(config={str(k): str(v) for k, v in config.items()}),
        )

    def _build_runtime_control_event(self, **kwargs) -> control_pb2.ControlEvent | None:
        return build_runtime_control_event(**kwargs)

    @staticmethod
    def _build_ping_event(*, event_id: str, **_kwargs) -> control_pb2.ControlEvent:
        now = time.time()
        return control_pb2.ControlEvent(
            event_id=event_id,
            ping=control_pb2.Ping(
                timestamp=Timestamp(seconds=int(now), nanos=int((now - int(now)) * 1e9)),
            ),
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
