"""
GatewayService gRPC 服务实现

实现 Gateway 服务的 RPC 方法。
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC
from typing import TYPE_CHECKING

import grpc
from loguru import logger

from antcode_gateway.handlers import HeartbeatHandler, LogHandler, ResultHandler, TaskPollHandler
from antcode_gateway.handlers.heartbeat import HeartbeatData

if TYPE_CHECKING:
    from antcode_core.domain.models import Worker


class GatewayServiceImpl:
    """GatewayService 实现

    处理 Worker 的心跳、状态报告等请求。
    """

    # 控制消息轮询间隔（秒）
    CONTROL_POLL_INTERVAL = 1.0

    def __init__(self):
        """初始化服务"""
        self.heartbeat_handler = HeartbeatHandler()
        self.result_handler = ResultHandler()
        self.log_handler = LogHandler()
        self.poll_handler = TaskPollHandler()
        self._active_streams: dict[str, asyncio.Queue] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}
        logger.info("GatewayService 已初始化")

    async def _control_poller(
        self,
        worker_id: str,
        response_queue: asyncio.Queue,
        stop_event: asyncio.Event,
    ) -> None:
        """后台协程：主动轮询控制消息并推送到响应队列

        Args:
            worker_id: Worker ID
            response_queue: 响应队列
            stop_event: 停止信号
        """
        from antcode_contracts import gateway_pb2

        redis = await self._get_redis_client()
        if redis is None:
            logger.warning(f"Worker {worker_id} 控制轮询器：Redis 不可用")
            return

        control_group = "antcode-control"
        consumer = worker_id

        # 确保消费者组存在
        streams = [f"antcode:control:{worker_id}", "antcode:control:global"]
        for stream_key in streams:
            try:
                await redis.xgroup_create(stream_key, control_group, id="0", mkstream=True)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    logger.warning(f"创建消费者组失败: {e}")

        logger.debug(f"Worker {worker_id} 控制轮询器已启动")

        while not stop_event.is_set():
            try:
                # 非阻塞轮询控制消息
                result = await redis.xreadgroup(
                    groupname=control_group,
                    consumername=consumer,
                    streams=dict.fromkeys(streams, ">"),
                    count=1,
                    block=int(self.CONTROL_POLL_INTERVAL * 1000),
                )

                if not result:
                    continue

                stream_key, messages = result[0]
                if not messages:
                    continue

                msg_id, data = messages[0]
                decoded = self._decode_data(data)
                control_type = decoded.get("control_type", "")

                # 构建控制消息
                master_msg = None

                if control_type in ("cancel", "kill"):
                    master_msg = gateway_pb2.MasterMessage(
                        task_cancel=gateway_pb2.TaskCancel(
                            task_id=decoded.get("task_id", ""),
                            execution_id=decoded.get("execution_id", decoded.get("run_id", "")),
                        )
                    )
                    logger.info(f"推送任务取消到 Worker {worker_id}: {decoded.get('task_id')}")

                elif control_type == "config_update":
                    config = decoded.get("config", {})
                    if isinstance(config, str):
                        import json
                        try:
                            config = json.loads(config)
                        except Exception:
                            config = {}
                    master_msg = gateway_pb2.MasterMessage(
                        config_update=gateway_pb2.ConfigUpdate(config=config)
                    )
                    logger.info(f"推送配置更新到 Worker {worker_id}")

                if master_msg:
                    await response_queue.put(master_msg)
                    # ACK 消息
                    await redis.xack(stream_key, control_group, msg_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} 控制轮询异常: {e}")
                await asyncio.sleep(self.CONTROL_POLL_INTERVAL)

        logger.debug(f"Worker {worker_id} 控制轮询器已停止")

    async def WorkerStream(
        self,
        request_iterator: AsyncIterator,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator:
        """双向流式通信

        处理 Worker 发送的消息（心跳、任务状态等），
        并向 Worker 发送消息（任务分发、取消等）。

        Args:
            request_iterator: Worker 发送的消息流
            context: gRPC 上下文

        Yields:
            MasterMessage: 发送给 Worker 的消息
        """

        worker_id = None
        response_queue: asyncio.Queue = asyncio.Queue()
        stop_event = asyncio.Event()
        poller_task: asyncio.Task | None = None

        try:
            # 处理来自 Worker 的消息
            async for message in request_iterator:
                try:
                    # 根据消息类型处理
                    payload_type = message.WhichOneof("payload")

                    if payload_type == "heartbeat":
                        heartbeat = message.heartbeat
                        worker_id = heartbeat.worker_id

                        # 注册活跃流并启动控制轮询器
                        if worker_id and worker_id not in self._active_streams:
                            self._active_streams[worker_id] = response_queue
                            # 启动后台控制轮询协程
                            poller_task = asyncio.create_task(
                                self._control_poller(worker_id, response_queue, stop_event)
                            )
                            self._stream_tasks[worker_id] = poller_task
                            logger.info(f"Worker {worker_id} 已连接，控制轮询器已启动")

                        # 处理心跳
                        heartbeat_data = HeartbeatData(
                            worker_id=heartbeat.worker_id,
                            status=heartbeat.status or "online",
                            version=heartbeat.version if heartbeat.version else "",
                        )

                        if heartbeat.HasField("metrics"):
                            m = heartbeat.metrics
                            heartbeat_data.cpu = m.cpu
                            heartbeat_data.memory = m.memory
                            heartbeat_data.disk = m.disk
                            heartbeat_data.running_tasks = m.running_tasks
                            heartbeat_data.max_concurrent_tasks = m.max_concurrent_tasks

                        if heartbeat.HasField("os_info"):
                            os = heartbeat.os_info
                            heartbeat_data.os_type = os.os_type
                            heartbeat_data.os_version = os.os_version
                            heartbeat_data.python_version = os.python_version
                            heartbeat_data.machine_arch = os.machine_arch

                        if heartbeat.capabilities:
                            heartbeat_data.capabilities = dict(heartbeat.capabilities)

                        await self.heartbeat_handler.handle(heartbeat_data)

                    elif payload_type == "task_status":
                        task_status = message.task_status
                        await self._handle_task_status(task_status)

                    elif payload_type == "task_ack":
                        task_ack = message.task_ack
                        logger.debug(
                            f"收到任务确认: task_id={task_ack.task_id}, "
                            f"accepted={task_ack.accepted}"
                        )

                    elif payload_type == "cancel_ack":
                        cancel_ack = message.cancel_ack
                        logger.debug(
                            f"收到取消确认: task_id={cancel_ack.task_id}, "
                            f"success={cancel_ack.success}"
                        )

                    # 检查是否有待发送的消息（非阻塞）
                    while not response_queue.empty():
                        response = await response_queue.get()
                        yield response

                except Exception as e:
                    logger.error(f"处理 WorkerStream 消息失败: {e}")

        except asyncio.CancelledError:
            logger.info(f"Worker {worker_id} 流被取消")
        except Exception as e:
            logger.error(f"WorkerStream 异常: {e}")
        finally:
            # 停止控制轮询器
            stop_event.set()
            if poller_task and not poller_task.done():
                poller_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller_task

            # 清理活跃流
            if worker_id:
                self._active_streams.pop(worker_id, None)
                self._stream_tasks.pop(worker_id, None)
                logger.info(f"Worker {worker_id} 已断开")

    async def _handle_task_status(self, task_status) -> None:
        """处理任务状态更新"""
        from datetime import datetime

        status_at = None
        if task_status.HasField("timestamp"):
            ts = task_status.timestamp
            total = ts.seconds + (ts.nanos / 1e9)
            status_at = datetime.fromtimestamp(total, tz=UTC)

        await self.result_handler.handle_status_update(
            run_id=task_status.execution_id,
            status=task_status.status,
            exit_code=task_status.exit_code if task_status.HasField("exit_code") else None,
            error_message=task_status.error_message
            if task_status.HasField("error_message")
            else None,
            timestamp=status_at,
        )

    async def Register(self, request, context):
        """处理节点注册请求

        Args:
            request: 注册请求 (gateway_pb2.RegisterRequest)
            context: gRPC 上下文

        Returns:
            注册响应 (gateway_pb2.RegisterResponse)
        """
        try:
            from antcode_contracts import gateway_pb2
            from antcode_gateway.config import gateway_config

            worker_id = request.worker_id
            api_key = request.api_key
            logger.info(
                f"收到注册请求: worker_id={worker_id}"
            )

            # 验证 API Key
            if not api_key:
                return gateway_pb2.RegisterResponse(
                    success=False,
                    error="缺少 API Key",
                )

            # 验证 API Key 和节点
            is_valid, error_msg, worker = await self._verify_registration(
                api_key=api_key,
                worker_id=worker_id,
            )

            if not is_valid:
                logger.warning(f"注册验证失败: worker_id={worker_id}, error={error_msg}")
                return gateway_pb2.RegisterResponse(
                    success=False,
                    error=error_msg,
                )

            logger.info(f"Worker 注册成功: worker_id={worker_id}")

            return gateway_pb2.RegisterResponse(
                success=True,
                worker_id=worker_id,
                heartbeat_interval=gateway_config.heartbeat_interval,
            )

        except Exception as e:
            logger.error(f"处理注册请求失败: {e}")
            from antcode_contracts import gateway_pb2

            return gateway_pb2.RegisterResponse(
                success=False,
                error=str(e),
            )

    async def _verify_registration(
        self,
        api_key: str,
        worker_id: str,
    ) -> tuple[bool, str, "Worker | None"]:
        """验证注册请求

        Args:
            api_key: API Key
            worker_id: Worker ID

        Returns:
            (是否有效, 错误消息, Worker 对象)
        """
        try:
            from antcode_core.domain.models import Worker

            # 1. 通过 API Key 查找 Worker
            worker = await Worker.filter(api_key=api_key).first()

            if not worker:
                # Fallback: 简单格式验证（开发环境）
                if api_key.startswith("ak_") and len(api_key) > 10:
                    logger.warning(f"API Key 未在数据库中找到，使用格式验证: {api_key[:12]}...")
                    return True, "", None
                return False, "无效的 API Key", None

            # 2. 验证 worker_id 匹配（如果提供）
            if worker_id and worker.public_id and worker_id != worker.public_id:
                return False, "Worker ID 不匹配", None

            return True, "", worker

        except ImportError:
            # antcode_core 不可用，使用简单验证
            logger.warning("antcode_core.domain.models 不可用，使用简单验证")
            if api_key.startswith("ak_") and len(api_key) > 10:
                return True, "", None
            return False, "无效的 API Key 格式", None
        except Exception as e:
            logger.error(f"验证注册请求异常: {e}")
            return False, f"验证失败: {e}", None

    async def PollTask(self, request, context):
        """Worker 拉取任务"""
        from antcode_contracts import gateway_pb2

        worker_id = request.worker_id
        timeout_ms = request.timeout_ms or 5000

        tasks = await self.poll_handler.handle(
            worker_id=worker_id,
            max_tasks=1,
            block_ms=timeout_ms,
        )

        if not tasks:
            return gateway_pb2.PollTaskResponse(has_task=False)

        task = tasks[0]
        task_dispatch = gateway_pb2.TaskDispatch(
            task_id=task.task_id,
            project_id=task.project_id,
            project_type=task.project_type,
            priority=task.priority,
            timeout=task.timeout,
            download_url=task.download_url,
            file_hash=task.file_hash,
            entry_point=task.entry_point,
            run_id=task.run_id,
        )

        if task.params:
            for key, value in task.params.items():
                task_dispatch.params[key] = str(value)

        if task.environment:
            for key, value in task.environment.items():
                task_dispatch.environment[key] = str(value)

        return gateway_pb2.PollTaskResponse(
            has_task=True,
            task=task_dispatch,
            receipt_id=task.receipt_id,
        )

    async def AckTask(self, request, context):
        """Worker 确认任务"""
        from antcode_contracts import gateway_pb2

        receipt_id = request.receipt_id or ""
        accepted = True
        if hasattr(request, "accepted"):
            accepted = request.accepted
        success = await self.poll_handler.ack_receipt(
            receipt_id,
            accepted=accepted,
            reason=request.reason if hasattr(request, "reason") else "",
        )

        return gateway_pb2.AckTaskResponse(
            success=success,
            error="" if success else "ack failed",
        )

    async def ReportResult(self, request, context):
        """Worker 上报结果"""
        from antcode_contracts import gateway_pb2
        from antcode_gateway.handlers.result import TaskResult as HandlerResult

        started_at = self._parse_datetime(request.started_at)
        finished_at = self._parse_datetime(request.finished_at)
        data = None
        if request.data_json:
            try:
                import json
                data = json.loads(request.data_json)
            except Exception:
                data = None

        result = HandlerResult(
            run_id=request.run_id or request.task_id,
            task_id=request.task_id,
            status=request.status,
            exit_code=request.exit_code,
            error_message=request.error_message,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=request.duration_ms,
            data=data,
        )

        success = await self.result_handler.handle(result)
        return gateway_pb2.ReportResultResponse(
            success=success,
            error="" if success else "update failed",
        )

    async def SendLog(self, request, context):
        """Worker 发送实时日志"""
        from antcode_contracts import gateway_pb2
        from antcode_gateway.handlers.logs import LogEntry as HandlerLog

        entry = HandlerLog(
            run_id=request.execution_id,
            log_type=request.log_type,
            content=request.content,
            sequence=request.sequence,
        )
        success = await self.log_handler.handle_realtime_log(entry)
        return gateway_pb2.SendLogResponse(
            success=success,
            error="" if success else "log failed",
        )

    async def SendLogBatch(self, request, context):
        """Worker 批量发送日志"""
        from antcode_contracts import gateway_pb2
        from antcode_gateway.handlers.logs import LogEntry as HandlerLog

        success = True
        for log in request.logs:
            entry = HandlerLog(
                run_id=log.execution_id,
                log_type=log.log_type,
                content=log.content,
                sequence=log.sequence,
            )
            ok = await self.log_handler.handle_realtime_log(entry)
            if not ok:
                success = False
        return gateway_pb2.SendLogBatchResponse(
            success=success,
            error="" if success else "log batch failed",
        )

    async def SendLogChunk(self, request, context):
        """Worker 发送日志分片"""
        from antcode_contracts import gateway_pb2
        from antcode_gateway.handlers.logs import LogChunk as HandlerChunk

        chunk = HandlerChunk(
            run_id=request.execution_id,
            log_type=request.log_type,
            chunk=request.data,
            offset=request.offset,
            is_final=request.is_final,
            checksum=request.checksum,
            total_size=request.total_size,
        )
        result = await self.log_handler.handle_log_chunk(chunk)
        return gateway_pb2.SendLogChunkResponse(
            success=result.get("ok", False),
            ack_offset=result.get("ack_offset", 0),
            error=result.get("error", ""),
        )

    async def SendHeartbeat(self, request, context):
        """Worker 发送心跳"""
        from antcode_contracts import gateway_pb2
        from antcode_gateway.handlers.heartbeat import HeartbeatData

        heartbeat = HeartbeatData(
            worker_id=request.worker_id,
            status=request.status,
            cpu=request.cpu_percent,
            memory=request.memory_percent,
            disk=request.disk_percent,
            running_tasks=request.running_tasks,
            max_concurrent_tasks=request.max_concurrent_tasks,
            version=request.version,
        )
        success = await self.heartbeat_handler.handle(heartbeat)
        return gateway_pb2.SendHeartbeatResponse(
            success=success,
            error="" if success else "heartbeat failed",
        )

    async def PollControl(self, request, context):
        """Worker 拉取控制消息"""
        from antcode_contracts import gateway_pb2
        redis = await self._get_redis_client()
        if redis is None:
            return gateway_pb2.PollControlResponse(has_control=False)

        worker_id = request.worker_id
        timeout_ms = request.timeout_ms or 5000
        control_group = "antcode-control"
        consumer = worker_id or "worker"
        streams = {
            f"antcode:control:{worker_id}": ">",
            "antcode:control:global": ">",
        }

        for stream_key in streams:
            try:
                await redis.xgroup_create(stream_key, control_group, id="0", mkstream=True)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    raise

        result = await redis.xreadgroup(
            groupname=control_group,
            consumername=consumer,
            streams=streams,
            count=1,
            block=timeout_ms,
        )

        if not result:
            return gateway_pb2.PollControlResponse(has_control=False)

        stream_key, messages = result[0]
        if not messages:
            return gateway_pb2.PollControlResponse(has_control=False)

        msg_id, data = messages[0]
        decoded = self._decode_data(data)
        control_type = decoded.get("control_type", "")
        receipt_id = f"{stream_key}|{msg_id}"

        if control_type in ("cancel", "kill"):
            control = gateway_pb2.ControlMessage(
                task_cancel=gateway_pb2.TaskCancel(
                    task_id=decoded.get("task_id", ""),
                    execution_id=decoded.get("execution_id", decoded.get("run_id", "")),
                )
            )
            return gateway_pb2.PollControlResponse(
                has_control=True,
                control=control,
                receipt_id=receipt_id,
            )

        if control_type == "config_update":
            control = gateway_pb2.ControlMessage(
                config_update=gateway_pb2.ConfigUpdate(config=decoded.get("config", {}))
            )
            return gateway_pb2.PollControlResponse(
                has_control=True,
                control=control,
                receipt_id=receipt_id,
            )

        if control_type == "runtime_manage":
            payload_raw = decoded.get("payload", "")
            if isinstance(payload_raw, dict):
                import json
                payload_json = json.dumps(payload_raw, ensure_ascii=False)
            else:
                payload_json = payload_raw or ""
            control = gateway_pb2.ControlMessage(
                runtime_control=gateway_pb2.RuntimeControl(
                    request_id=decoded.get("request_id", ""),
                    action=decoded.get("action", ""),
                    reply_stream=decoded.get("reply_stream", ""),
                    payload_json=payload_json,
                )
            )
            return gateway_pb2.PollControlResponse(
                has_control=True,
                control=control,
                receipt_id=receipt_id,
            )

        return gateway_pb2.PollControlResponse(has_control=False)

    async def AckControl(self, request, context):
        """确认控制消息"""
        from antcode_contracts import gateway_pb2

        redis = await self._get_redis_client()
        if redis is None or "|" not in request.receipt_id:
            return gateway_pb2.AckControlResponse(success=False, error="redis unavailable")

        stream_key, msg_id = request.receipt_id.split("|", 1)
        try:
            await redis.xack(stream_key, "antcode-control", msg_id)
            return gateway_pb2.AckControlResponse(success=True, error="")
        except Exception as e:
            return gateway_pb2.AckControlResponse(success=False, error=str(e))

    async def ReportControlResult(self, request, context):
        """Worker 回传控制结果"""
        from antcode_contracts import gateway_pb2

        redis = await self._get_redis_client()
        if redis is None:
            return gateway_pb2.ControlResultResponse(success=False, error="redis unavailable")

        reply_stream = request.reply_stream or f"antcode:control:reply:{request.request_id}"
        payload = {
            "request_id": request.request_id,
            "success": str(bool(request.success)).lower(),
            "data": request.payload_json or "",
            "error": request.error or "",
        }

        try:
            await redis.xadd(reply_stream, payload, maxlen=1, approximate=True)
            await redis.expire(reply_stream, 120)
            return gateway_pb2.ControlResultResponse(success=True, error="")
        except Exception as e:
            return gateway_pb2.ControlResultResponse(success=False, error=str(e))

    async def _get_redis_client(self):
        try:
            from antcode_core.infrastructure.redis import get_redis_client

            return await get_redis_client()
        except ImportError:
            logger.warning("antcode_core.infrastructure.redis 不可用")
            return None

    def _decode_data(self, data: dict) -> dict:
        decoded = {}
        for k, v in data.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            decoded[key] = val
        if "config" in decoded and isinstance(decoded["config"], str):
            try:
                import json
                decoded["config"] = json.loads(decoded["config"])
            except Exception:
                pass
        if "payload" in decoded and isinstance(decoded["payload"], str):
            try:
                import json
                decoded["payload"] = json.loads(decoded["payload"])
            except Exception:
                pass
        return decoded

    def _parse_datetime(self, value: str):
        if not value:
            return None
        from datetime import datetime

        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None
