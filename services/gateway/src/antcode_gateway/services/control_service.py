"""
ControlService gRPC 服务实现 (P1c)

负责 Worker 生命周期 (Register/Deregister)、租约 (Lease)、任务取消
(CancelTask)、配置更新 (UpdateConfig) 以及反向控制通道
(WatchControl / AckControl)。

P1c 范围只覆盖契约和 Stream 路由：

- ``Register`` 沿用旧 ``GatewayServiceImpl.Register`` 的 API Key 验证逻辑，
  额外返回 ``lease_ttl_ms`` / ``lease_renew_after_ms``。真实 LeaseStore
  状态机由 P3 接管，这里只占位返回固定 30s TTL。
- ``Lease`` 暂时返回伪 lease_id（``lease-{worker_id}-{ts}``）+ 30s 过期；
  同时把 ``request.metrics`` 写到 ``heartbeat:{worker_id}`` Hash，保留运维
  dashboard 可见。
- ``CancelTask`` / ``UpdateConfig`` 把控制指令 xadd 到对应
  ``control:{worker_id}`` Stream（payload 与 control_plane helpers 对齐），
  由 ``WatchControl`` 消费推送。
- ``WatchControl`` 是 server-streaming：从
  ``control:{worker_id}`` + ``control:global`` 两个 Stream
  ``XREADGROUP`` 后封装成 ``ControlEvent`` yield 给 Worker。
- ``AckControl`` XACK 对应 stream 的 message。``event_id`` 编码格式
  ``{stream_key}|{redis_msg_id}``，与服务端 yield 时使用的 ``event_id`` 一致。
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import grpc
from antcode_contracts import control_pb2
from antcode_contracts.common_pb2 import Timestamp
from antcode_contracts.control_pb2_grpc import ControlServiceServicer
from antcode_core.infrastructure.redis import (
    build_cancel_control_payload,
    build_config_update_control_payload,
    control_global_stream,
    control_group,
    control_stream,
    decode_stream_payload,
    get_redis_client,
)
from loguru import logger

from antcode_gateway.handlers import LeaseHandler

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


class GatewayControlService(ControlServiceServicer):
    """ControlService Gateway 端实现。

    P3：可选注入 ``LeaseStore``，接管 Lease 状态机。注入后：
    - ``Register`` 成功时为 Worker 发首个 lease。
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
        # P2-#21: event_id 不再直接暴露 stream key/msg id, 用 server 端短 token
        # 映射, AckControl 时反查。
        self._event_id_map: dict[str, tuple[str, str]] = {}
        self._event_id_lock = asyncio.Lock()
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

        is_valid, error_msg, _worker = await self._verify_registration(
            api_key=api_key, worker_id=worker_id
        )
        if not is_valid:
            logger.warning(
                f"Register 验证失败: worker_id={worker_id}, error={error_msg}"
            )
            return control_pb2.RegisterResponse(success=False, error=error_msg)

        # 注册成功后立刻发首个 lease（若已注入 LeaseStore），
        # 让 Worker 拿到注册响应就已经在 active 集合里。
        ttl_ms = DEFAULT_LEASE_TTL_MS
        renew_after_ms = DEFAULT_LEASE_RENEW_AFTER_MS
        if self._lease_store is not None:
            try:
                await self._lease_store.grant(worker_id, current_lease_id="")
                ttl_ms = self._lease_store.policy.ttl_ms
                renew_after_ms = self._lease_store.policy.renew_after_ms
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"Register 阶段发首个 lease 失败（不阻塞注册）: {exc}")

        logger.info(f"Worker 注册成功: worker_id={worker_id}")
        return control_pb2.RegisterResponse(
            success=True,
            worker_id=worker_id,
            lease_ttl_ms=ttl_ms,
            lease_renew_after_ms=renew_after_ms,
        )

    async def _verify_registration(
        self,
        api_key: str,
        worker_id: str,
    ) -> tuple[bool, str, Worker | None]:
        """与旧 GatewayServiceImpl 行为一致的注册校验。"""
        try:
            from antcode_core.domain.models import Worker

            worker = await Worker.filter(api_key=api_key).first()
            if not worker:
                return False, "无效的 API Key", None
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
        worker_id = request.worker_id
        reason = request.reason or "explicit"
        logger.info(f"Worker Deregister: worker_id={worker_id}, reason={reason}")
        # 主动撤销 lease（让 Master 端立刻看到 worker 下线）。
        if self._lease_store is not None:
            try:
                await self._lease_store.revoke(worker_id, reason=f"deregister:{reason}")
            except Exception as exc:
                logger.warning(f"Deregister 撤销 lease 失败: {exc}")
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
        worker_id = request.worker_id
        if not worker_id:
            return control_pb2.LeaseResponse(
                lease_id="",
                expires_at_ms=0,
                renew_after_ms=DEFAULT_LEASE_RENEW_AFTER_MS,
                revoked=True,
                revoke_reason="worker_id 为空",
            )

        # 把 metrics 同步落 heartbeat:{worker_id} Hash —— 过渡期保留运维 dashboard 视图。
        metrics_dict: dict | None = None
        if request.HasField("metrics"):
            metrics = request.metrics
            metrics_dict = {
                "cpu": metrics.cpu,
                "memory": metrics.memory,
                "disk": metrics.disk,
                "running_tasks": metrics.running_tasks,
                "max_concurrent_tasks": metrics.max_concurrent_tasks,
            }
            from antcode_gateway.handlers.heartbeat import LeaseData

            lease_data = LeaseData(
                worker_id=worker_id,
                status="online",
                cpu=metrics.cpu,
                memory=metrics.memory,
                disk=metrics.disk,
                running_tasks=metrics.running_tasks,
                max_concurrent_tasks=metrics.max_concurrent_tasks,
            )
            try:
                await self._lease_handler.handle(lease_data)
            except Exception as exc:
                logger.warning(f"Lease 写 Redis 心跳 Hash 失败: {exc}")

        # 主路径：LeaseStore.grant
        if self._lease_store is not None:
            try:
                lease = await self._lease_store.grant(
                    worker_id,
                    current_lease_id=request.current_lease_id or "",
                    metrics=metrics_dict,
                )
                return control_pb2.LeaseResponse(
                    lease_id=lease.lease_id,
                    expires_at_ms=lease.expires_at_ms,
                    renew_after_ms=self._lease_store.policy.renew_after_ms,
                    revoked=False,
                )
            except Exception as exc:
                logger.exception(f"Lease grant 失败，降级为占位响应: worker_id={worker_id}, exc={exc}")
                # 不要直接 abort —— 让 Worker 拿到非 revoked 响应继续运行，
                # 下一次 sweep 自然剔除。

        # 兜底（LeaseStore 未注入或 grant 异常）：占位 30s lease。
        now_ms = int(time.time() * 1000)
        placeholder_lease_id = f"lease-{worker_id}-{now_ms}"
        expires_at_ms = now_ms + DEFAULT_LEASE_TTL_MS
        logger.debug(
            f"Lease 占位响应: worker_id={worker_id}, lease_id={placeholder_lease_id}, "
            f"expires_at_ms={expires_at_ms}"
        )
        return control_pb2.LeaseResponse(
            lease_id=placeholder_lease_id,
            expires_at_ms=expires_at_ms,
            renew_after_ms=DEFAULT_LEASE_RENEW_AFTER_MS,
            revoked=False,
        )

    # =========================================================================
    # CancelTask / UpdateConfig - 推到 control stream
    # =========================================================================

    async def CancelTask(
        self,
        request: control_pb2.CancelTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.CancelTaskResponse:
        worker_id = request.worker_id
        # P2-#19: 协议违规 (参数缺失 / redis 不可用) 走 gRPC error。
        if not worker_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "worker_id 不能为空"
            )
            return control_pb2.CancelTaskResponse(success=False, error="worker_id 不能为空")

        payload = build_cancel_control_payload(
            run_id=request.run_id or request.task_id,
            reason=request.reason,
            task_id=request.task_id or request.run_id,
        )
        try:
            redis = await get_redis_client()
            if redis is None:
                await context.abort(
                    grpc.StatusCode.UNAVAILABLE, "redis unavailable"
                )
                return control_pb2.CancelTaskResponse(
                    success=False, error="redis unavailable"
                )
            # P1-#8: 限制 control stream 长度,避免无界增长。
            await redis.xadd(
                control_stream(worker_id),
                payload,
                maxlen=CONTROL_STREAM_MAXLEN,
                approximate=True,
            )
            logger.info(
                f"已下发任务取消到 control:{worker_id}: task_id={request.task_id}"
            )
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
        worker_id = request.worker_id
        # P2-#19: 协议违规 (参数缺失 / redis 不可用) 走 gRPC error。
        if not worker_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "worker_id 不能为空"
            )
            return control_pb2.UpdateConfigResponse(
                success=False, error="worker_id 不能为空"
            )

        config = dict(request.config) if request.config else {}
        payload = build_config_update_control_payload(config)
        try:
            redis = await get_redis_client()
            if redis is None:
                await context.abort(
                    grpc.StatusCode.UNAVAILABLE, "redis unavailable"
                )
                return control_pb2.UpdateConfigResponse(
                    success=False, error="redis unavailable"
                )
            # P1-#8: 限制 control stream 长度,避免无界增长。
            await redis.xadd(
                control_stream(worker_id),
                payload,
                maxlen=CONTROL_STREAM_MAXLEN,
                approximate=True,
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
        worker_id = request.worker_id
        if not worker_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "worker_id 不能为空")
            return

        redis = await get_redis_client()
        if redis is None:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "redis unavailable")
            return

        group = control_group()
        streams = [control_stream(worker_id), control_global_stream()]
        for stream_key in streams:
            await self._ensure_control_group(redis, stream_key, group)

        consumer = worker_id
        logger.info(f"WatchControl 已建立: worker_id={worker_id}")

        try:
            while True:
                # 客户端取消即退出
                if context.cancelled():
                    break

                try:
                    result = await redis.xreadgroup(
                        groupname=group,
                        consumername=consumer,
                        streams=dict.fromkeys(streams, ">"),
                        count=1,
                        block=CONTROL_POLL_BLOCK_MS,
                    )
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.exception(f"WatchControl xreadgroup 异常: {exc}")
                    await asyncio.sleep(1.0)
                    continue

                if not result:
                    continue

                for stream_data in result:
                    stream_key = stream_data[0]
                    if isinstance(stream_key, bytes):
                        stream_key = stream_key.decode()
                    messages = stream_data[1]
                    for msg_id, data in messages:
                        if isinstance(msg_id, bytes):
                            msg_id = msg_id.decode()
                        event = await self._build_control_event(
                            stream_key=stream_key,
                            msg_id=msg_id,
                            data=data,
                        )
                        if event is None:
                            # 未识别的 control_type - 直接 ack 避免阻塞
                            with contextlib.suppress(Exception):
                                await redis.xack(stream_key, group, msg_id)
                            continue
                        yield event

        except asyncio.CancelledError:
            logger.info(f"WatchControl 被取消: worker_id={worker_id}")
            raise
        finally:
            logger.info(f"WatchControl 已断开: worker_id={worker_id}")

    async def _build_control_event(
        self,
        stream_key: str,
        msg_id: str,
        data: dict,
    ) -> control_pb2.ControlEvent | None:
        """把 Stream 一条消息封装成 ``ControlEvent``。

        P2-#21: event_id 是 server 端生成的短 token (secrets.token_hex(8)),
        在 ``_event_id_map`` 维护 token -> (stream_key, msg_id) 的映射,
        AckControl 时反查。不再把 stream key 明文塞回客户端。
        """
        try:
            decoded = decode_stream_payload(data)
        except Exception as exc:
            logger.exception(f"control stream payload 解码失败: {exc}")
            return None

        event_id = await self._register_event_id(stream_key, msg_id)
        control_type = decoded.get("control_type", "")

        if control_type in ("cancel", "kill"):
            return control_pb2.ControlEvent(
                event_id=event_id,
                task_cancel=control_pb2.TaskCancel(
                    task_id=str(decoded.get("task_id", "")),
                    run_id=str(decoded.get("run_id", "")),
                    reason=str(decoded.get("reason", "")),
                ),
            )

        if control_type == "config_update":
            config = decoded.get("config") or {}
            if not isinstance(config, dict):
                config = {}
            return control_pb2.ControlEvent(
                event_id=event_id,
                config_update=control_pb2.ConfigUpdate(
                    config={str(k): str(v) for k, v in config.items()},
                ),
            )

        if control_type == "runtime_manage":
            params: dict[str, str] = {}
            reply_stream = decoded.get("reply_stream")
            if reply_stream:
                params["reply_stream"] = str(reply_stream)

            # 把旧 payload_json 折叠成 GenericAction.args（string->string）
            payload_raw = decoded.get("payload")
            args: dict[str, str] = {}
            if isinstance(payload_raw, dict):
                for k, v in payload_raw.items():
                    if v is None:
                        args[str(k)] = ""
                    elif isinstance(v, (str, int, float, bool)):
                        args[str(k)] = str(v)
                    else:
                        # 嵌套结构降级为 JSON 字符串放进 args
                        try:
                            import json

                            args[str(k)] = json.dumps(v, ensure_ascii=False)
                        except Exception:
                            args[str(k)] = repr(v)

            runtime_control = control_pb2.RuntimeControl(
                request_id=str(decoded.get("request_id", "")),
                action=str(decoded.get("action", "")),
                params=params,
                action_typed=control_pb2.RuntimeAction(
                    generic=control_pb2.GenericAction(
                        name=str(decoded.get("action", "")),
                        args=args,
                    ),
                ),
            )
            return control_pb2.ControlEvent(
                event_id=event_id,
                runtime_control=runtime_control,
            )

        if control_type == "ping":
            now = time.time()
            return control_pb2.ControlEvent(
                event_id=event_id,
                ping=control_pb2.Ping(
                    timestamp=Timestamp(
                        seconds=int(now),
                        nanos=int((now - int(now)) * 1e9),
                    ),
                ),
            )

        logger.warning(
            f"未识别的 control_type: stream={stream_key} msg_id={msg_id} "
            f"control_type={control_type}"
        )
        return None

    async def AckControl(
        self,
        request: control_pb2.AckControlRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.AckControlResponse:
        event_id = request.event_id
        if not event_id:
            return control_pb2.AckControlResponse(received=False)

        # P2-#21: 反查 server 端映射拿到真实 stream_key/msg_id;
        # 兼容旧 "stream|msg" 形式以平滑滚动升级。
        resolved = await self._resolve_event_id(event_id)
        if resolved is None:
            if "|" in event_id:
                stream_key, msg_id = event_id.split("|", 1)
            else:
                logger.warning(f"AckControl 未知 event_id: {event_id}")
                return control_pb2.AckControlResponse(received=False)
        else:
            stream_key, msg_id = resolved

        try:
            redis = await get_redis_client()
            if redis is None:
                return control_pb2.AckControlResponse(received=False)
            acked = await redis.xack(stream_key, control_group(), msg_id)
            received = int(acked or 0) > 0
            if not received:
                logger.warning(
                    f"AckControl 未命中: stream={stream_key} msg_id={msg_id}"
                )
            if not request.success and request.error:
                logger.warning(
                    f"Worker 上报控制事件执行失败: event_id={event_id} error={request.error}"
                )
            return control_pb2.AckControlResponse(received=received)
        except Exception as exc:
            logger.exception(f"AckControl 异常: {exc}")
            return control_pb2.AckControlResponse(received=False)

    # =========================================================================
    # 内部工具
    # =========================================================================

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

    async def _register_event_id(self, stream_key: str, msg_id: str) -> str:
        """P2-#21: 生成短 token 作为对外 event_id, 服务器端维护映射。

        与旧 ``{stream}|{msg}`` 形式不同, 这里不暴露 redis stream key。
        worker 端 AckControl 时只看到 token, 由 _resolve_event_id 反查。
        """
        token = secrets.token_hex(8)
        async with self._event_id_lock:
            # 限制 map 大小, 简单防御内存溢出: 超过 10000 条时清掉最早一半。
            if len(self._event_id_map) > 10_000:
                # dict 在 3.7+ 是有序的, 直接砍前一半 keys。
                cutoff = len(self._event_id_map) // 2
                stale_keys = list(self._event_id_map.keys())[:cutoff]
                for k in stale_keys:
                    self._event_id_map.pop(k, None)
            self._event_id_map[token] = (stream_key, msg_id)
        return token

    async def _resolve_event_id(
        self, event_id: str
    ) -> tuple[str, str] | None:
        """根据 token 反查 (stream_key, msg_id)。命中后即从 map 移除避免重放。"""
        async with self._event_id_lock:
            return self._event_id_map.pop(event_id, None)
