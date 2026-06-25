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
    from antcode_core.domain.models import Worker

# 占位常量 - P3 接管后由 LeaseStore 决定
DEFAULT_LEASE_TTL_MS = 30_000
DEFAULT_LEASE_RENEW_AFTER_MS = 10_000

# WatchControl 内部轮询间隔（block 毫秒）
CONTROL_POLL_BLOCK_MS = 1_000


class GatewayControlService(ControlServiceServicer):
    """ControlService Gateway 端实现。"""

    def __init__(self, lease_handler: LeaseHandler | None = None):
        self._lease_handler = lease_handler or LeaseHandler()
        self._control_group_lock = asyncio.Lock()
        self._initialized_control_groups: set[tuple[str, str]] = set()
        logger.info("ControlService 已初始化")

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

        logger.info(f"Worker 注册成功: worker_id={worker_id}")
        return control_pb2.RegisterResponse(
            success=True,
            worker_id=worker_id,
            lease_ttl_ms=DEFAULT_LEASE_TTL_MS,
            lease_renew_after_ms=DEFAULT_LEASE_RENEW_AFTER_MS,
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
            logger.error("antcode_core.domain.models 不可用，无法验证注册 API Key")
            return False, "Worker/API Key 验证服务不可用", None
        except Exception as exc:
            logger.error(f"验证注册请求异常: {exc}")
            return False, f"验证失败: {exc}", None

    async def Deregister(
        self,
        request: control_pb2.DeregisterRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.DeregisterResponse:
        worker_id = request.worker_id
        reason = request.reason or "explicit"
        logger.info(f"Worker Deregister: worker_id={worker_id}, reason={reason}")
        # P3 LeaseStore 接管后这里会撤销 lease；当前只清理 heartbeat Hash 的 TTL。
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
        worker_id = request.worker_id
        now_ms = int(time.time() * 1000)
        lease_id = f"lease-{worker_id}-{now_ms}"
        expires_at_ms = now_ms + DEFAULT_LEASE_TTL_MS

        # Metrics 落 Redis Hash 保持过渡期 dashboard 可见
        if request.HasField("metrics"):
            metrics = request.metrics
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

        logger.debug(
            f"Lease granted: worker_id={worker_id}, lease_id={lease_id}, "
            f"expires_at_ms={expires_at_ms}"
        )
        return control_pb2.LeaseResponse(
            lease_id=lease_id,
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
        if not worker_id:
            return control_pb2.CancelTaskResponse(success=False, error="worker_id 不能为空")

        payload = build_cancel_control_payload(
            run_id=request.run_id or request.task_id,
            reason=request.reason,
            task_id=request.task_id or request.run_id,
        )
        try:
            redis = await get_redis_client()
            if redis is None:
                return control_pb2.CancelTaskResponse(
                    success=False, error="redis unavailable"
                )
            await redis.xadd(control_stream(worker_id), payload)
            logger.info(
                f"已下发任务取消到 control:{worker_id}: task_id={request.task_id}"
            )
            return control_pb2.CancelTaskResponse(success=True)
        except Exception as exc:
            logger.error(f"CancelTask 写 Stream 失败: {exc}")
            return control_pb2.CancelTaskResponse(success=False, error=str(exc))

    async def UpdateConfig(
        self,
        request: control_pb2.UpdateConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> control_pb2.UpdateConfigResponse:
        worker_id = request.worker_id
        if not worker_id:
            return control_pb2.UpdateConfigResponse(
                success=False, error="worker_id 不能为空"
            )

        config = dict(request.config) if request.config else {}
        payload = build_config_update_control_payload(config)
        try:
            redis = await get_redis_client()
            if redis is None:
                return control_pb2.UpdateConfigResponse(
                    success=False, error="redis unavailable"
                )
            await redis.xadd(control_stream(worker_id), payload)
            logger.info(f"已下发配置更新到 control:{worker_id}")
            return control_pb2.UpdateConfigResponse(success=True)
        except Exception as exc:
            logger.error(f"UpdateConfig 写 Stream 失败: {exc}")
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
                    logger.error(f"WatchControl xreadgroup 异常: {exc}")
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
                        event = self._build_control_event(
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

    def _build_control_event(
        self,
        stream_key: str,
        msg_id: str,
        data: dict,
    ) -> control_pb2.ControlEvent | None:
        """把 Stream 一条消息封装成 ``ControlEvent``。

        event_id 形式 ``{stream_key}|{msg_id}``，与 AckControl 的解析对齐。
        """
        try:
            decoded = decode_stream_payload(data)
        except Exception as exc:
            logger.error(f"control stream payload 解码失败: {exc}")
            return None

        event_id = f"{stream_key}|{msg_id}"
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
        if not event_id or "|" not in event_id:
            return control_pb2.AckControlResponse(received=False)

        stream_key, msg_id = event_id.split("|", 1)
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
            logger.error(f"AckControl 异常: {exc}")
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
                    logger.error(f"创建 control 消费者组失败: {exc}")
                    raise
            self._initialized_control_groups.add(key)
