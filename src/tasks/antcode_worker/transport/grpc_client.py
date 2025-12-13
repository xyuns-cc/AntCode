"""
gRPC 传输客户端

基于 grpcio 实现的 gRPC 传输协议。
支持双向流通信，用于与 Master 的实时通信。

Requirements: 1.2, 1.3
"""

import asyncio
import gzip
import time
from datetime import datetime
from typing import Optional, Callable, Awaitable, List, AsyncIterator

import grpc
from loguru import logger

# 使用 worker 本地的 grpc_generated 模块
from ..grpc_generated import node_service_pb2 as pb2
from ..grpc_generated import node_service_pb2_grpc as pb2_grpc
from ..grpc_generated import common_pb2

from ..domain.models import (
    ConnectionConfig,
    Heartbeat,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskCancel,
    TaskAck,
    CancelAck,
    GrpcMetrics,
    Metrics,
    OSInfo,
)
from .protocol import TransportProtocol, ConnectionError, SendError


# 压缩阈值（字节）
COMPRESS_THRESHOLD = 1024


class GrpcClient(TransportProtocol):
    """
    gRPC 客户端实现
    
    实现 TransportProtocol 接口，使用 gRPC 双向流与 Master 通信。
    
    特性:
    - 双向流通信
    - 自动重连
    - 消息压缩
    - 指标统计
    
    Requirements: 1.2, 1.3
    """

    def __init__(self):
        self._config: Optional[ConnectionConfig] = None
        self._channel: Optional[grpc.aio.Channel] = None
        self._stub: Optional[pb2_grpc.NodeServiceStub] = None
        self._stream: Optional[grpc.aio.StreamStreamCall] = None
        self._connected = False
        self._metrics = GrpcMetrics()
        
        # 消息发送队列
        self._send_queue: asyncio.Queue = asyncio.Queue()
        
        # 回调函数
        self._on_task_dispatch: Optional[Callable[[TaskDispatch], Awaitable[None]]] = None
        self._on_task_cancel: Optional[Callable[[TaskCancel], Awaitable[None]]] = None
        
        # 后台任务
        self._stream_task: Optional[asyncio.Task] = None
        self._send_task: Optional[asyncio.Task] = None
        self._running = False
        
        # 心跳间隔（从 Master 获取）
        self._heartbeat_interval = 30

    @property
    def protocol_name(self) -> str:
        return "grpc"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._channel is not None

    @property
    def metrics(self) -> GrpcMetrics:
        return self._metrics

    @property
    def heartbeat_interval(self) -> int:
        """获取心跳间隔"""
        return self._heartbeat_interval

    async def connect(self, config: ConnectionConfig) -> bool:
        """
        建立 gRPC 连接
        
        1. 创建 gRPC channel
        2. 调用 Register RPC 注册节点
        3. 建立双向流
        
        Requirements: 1.2
        """
        self._config = config
        
        try:
            # 解析 gRPC 地址
            grpc_target = self._get_grpc_target(config)
            logger.info(f"正在连接 gRPC 服务器: {grpc_target}")
            
            # 创建 channel
            self._channel = grpc.aio.insecure_channel(
                grpc_target,
                options=[
                    ('grpc.max_send_message_length', 50 * 1024 * 1024),
                    ('grpc.max_receive_message_length', 50 * 1024 * 1024),
                    ('grpc.keepalive_time_ms', 30000),
                    ('grpc.keepalive_timeout_ms', 10000),
                    ('grpc.keepalive_permit_without_calls', True),
                    ('grpc.http2.min_time_between_pings_ms', 10000),
                ]
            )
            
            # 创建 stub
            self._stub = pb2_grpc.NodeServiceStub(self._channel)
            
            # 注册节点
            register_success = await self._register_node()
            if not register_success:
                await self._close_channel()
                return False
            
            # 建立双向流
            await self._start_stream()
            
            self._connected = True
            self._metrics.record_connection()
            
            logger.info(f"gRPC 连接成功: {grpc_target}")
            return True
            
        except grpc.aio.AioRpcError as e:
            self._metrics.record_error(
                error_message=str(e.details()),
                error_code=str(e.code()),
                operation="connect",
            )
            await self._close_channel()
            raise ConnectionError(f"gRPC 连接失败: {e.details()}")
        except Exception as e:
            self._metrics.record_error(
                error_message=str(e),
                operation="connect",
            )
            await self._close_channel()
            raise ConnectionError(f"gRPC 连接异常: {e}")

    def _get_grpc_target(self, config: ConnectionConfig) -> str:
        """从配置中获取 gRPC 目标地址"""
        # 从 master_url 提取主机名
        url = config.master_url
        if url.startswith("http://"):
            url = url[7:]
        elif url.startswith("https://"):
            url = url[8:]
        
        # 移除路径部分
        if "/" in url:
            url = url.split("/")[0]
        
        # 移除端口部分
        if ":" in url:
            host = url.split(":")[0]
        else:
            host = url
        
        return f"{host}:{config.grpc_port}"

    async def _register_node(self) -> bool:
        """注册节点到 Master"""
        if not self._stub or not self._config:
            return False
        
        try:
            import platform
            
            os_info = common_pb2.OSInfo(
                os_type=platform.system(),
                os_version=platform.release(),
                python_version=platform.python_version(),
                machine_arch=platform.machine(),
            )
            
            request = pb2.RegisterRequest(
                machine_code=self._config.machine_code,
                api_key=self._config.api_key,
                node_id=self._config.node_id,
                os_info=os_info,
                capabilities={},
            )
            
            # 添加认证 metadata
            metadata = [
                ('authorization', f'Bearer {self._config.api_key}'),
                ('x-node-id', self._config.node_id),
                ('x-machine-code', self._config.machine_code),
            ]
            
            response = await self._stub.Register(request, metadata=metadata, timeout=10)
            
            if response.success:
                logger.info(f"节点注册成功: node_id={response.node_id}")
                if response.heartbeat_interval > 0:
                    self._heartbeat_interval = response.heartbeat_interval
                return True
            else:
                logger.error(f"节点注册失败: {response.error}")
                return False
                
        except grpc.aio.AioRpcError as e:
            logger.error(f"节点注册 RPC 失败: {e.code()} - {e.details()}")
            return False
        except Exception as e:
            logger.error(f"节点注册异常: {e}")
            return False

    async def _start_stream(self):
        """启动双向流"""
        self._running = True
        
        # 启动发送任务
        self._send_task = asyncio.create_task(self._send_loop())
        
        # 启动接收任务
        self._stream_task = asyncio.create_task(self._stream_loop())

    async def _send_loop(self):
        """发送消息循环"""
        while self._running:
            try:
                # 从队列获取消息
                message = await asyncio.wait_for(
                    self._send_queue.get(),
                    timeout=1.0
                )
                
                if message is None:
                    continue
                
                # 发送消息
                if self._stream:
                    await self._stream.write(message)
                    self._metrics.record_message_sent(message.ByteSize())
                    
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"发送消息异常: {e}")
                await asyncio.sleep(0.1)

    async def _stream_loop(self):
        """
        双向流处理循环
        
        接收来自 Master 的消息并分发到对应的处理器。
        
        Requirements: 1.3
        """
        if not self._stub or not self._config:
            return
        
        try:
            # 添加认证 metadata
            metadata = [
                ('authorization', f'Bearer {self._config.api_key}'),
                ('x-node-id', self._config.node_id),
                ('x-machine-code', self._config.machine_code),
            ]
            
            # 创建双向流
            self._stream = self._stub.NodeStream(
                self._message_generator(),
                metadata=metadata,
            )
            
            # 接收消息
            async for message in self._stream:
                if not self._running:
                    break
                
                self._metrics.record_message_received(message.ByteSize())
                
                await self._handle_master_message(message)
                
        except grpc.aio.AioRpcError as e:
            if self._running:
                self._metrics.record_error(
                    error_message=str(e.details()),
                    error_code=str(e.code()),
                    operation="stream_loop",
                )
                self._metrics.record_disconnection()
                self._connected = False
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._running:
                self._metrics.record_error(
                    error_message=str(e),
                    operation="stream_loop",
                )
                self._metrics.record_disconnection()
                self._connected = False

    async def _message_generator(self) -> AsyncIterator[pb2.NodeMessage]:
        """消息生成器，用于双向流的发送端"""
        while self._running:
            try:
                message = await asyncio.wait_for(
                    self._send_queue.get(),
                    timeout=1.0
                )
                if message is not None:
                    yield message
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def _handle_master_message(self, message: pb2.MasterMessage):
        """
        处理来自 Master 的消息
        
        根据消息类型分发到对应的处理器。
        """
        payload_type = message.WhichOneof('payload')
        
        if payload_type == 'task_dispatch':
            await self._handle_task_dispatch(message.task_dispatch)
        elif payload_type == 'task_cancel':
            await self._handle_task_cancel(message.task_cancel)
        elif payload_type == 'config_update':
            await self._handle_config_update(message.config_update)
        elif payload_type == 'ping':
            await self._handle_ping(message.ping)
        else:
            logger.warning(f"未知消息类型: {payload_type}")

    async def _handle_task_dispatch(self, dispatch: pb2.TaskDispatch):
        """处理任务分发消息"""
        task = TaskDispatch(
            task_id=dispatch.task_id,
            project_id=dispatch.project_id,
            project_type=dispatch.project_type,
            priority=dispatch.priority,
            params=dict(dispatch.params),
            environment=dict(dispatch.environment),
            timeout=dispatch.timeout,
            download_url=dispatch.download_url or None,
            file_hash=dispatch.file_hash or None,
            entry_point=dispatch.entry_point or None,
        )
        
        logger.info(f"收到任务分发: task_id={task.task_id}")
        
        if self._on_task_dispatch:
            await self._on_task_dispatch(task)

    async def _handle_task_cancel(self, cancel: pb2.TaskCancel):
        """处理任务取消消息"""
        task_cancel = TaskCancel(
            task_id=cancel.task_id,
            execution_id=cancel.execution_id,
        )
        
        logger.info(f"收到任务取消: task_id={task_cancel.task_id}")
        
        if self._on_task_cancel:
            await self._on_task_cancel(task_cancel)

    async def _handle_config_update(self, config: pb2.ConfigUpdate):
        """处理配置更新消息"""
        logger.info(f"收到配置更新: {dict(config.config)}")
        # TODO: 实现配置更新逻辑

    async def _handle_ping(self, ping: pb2.Ping):
        """处理 Ping 消息"""
        logger.debug("收到 Ping 消息")
        # Ping 消息用于保持连接活跃，无需特殊处理

    async def disconnect(self):
        """断开 gRPC 连接"""
        self._running = False
        self._connected = False
        self._metrics.record_disconnection()
        
        # 取消后台任务
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
        
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        
        # 关闭流
        if self._stream:
            try:
                await self._stream.done_writing()
            except Exception:
                pass
            self._stream = None
        
        # 关闭 channel
        await self._close_channel()
        
        logger.info("gRPC 连接已断开")

    async def _close_channel(self):
        """关闭 gRPC channel"""
        if self._channel:
            try:
                await self._channel.close()
            except Exception:
                pass
            self._channel = None
            self._stub = None

    async def send_heartbeat(self, heartbeat: Heartbeat) -> bool:
        """
        发送心跳消息
        
        Requirements: 2.2, 2.3
        """
        if not self.is_connected:
            return False
        
        try:
            start_time = time.time()
            
            # 构建 protobuf 消息
            pb_heartbeat = pb2.Heartbeat(
                node_id=heartbeat.node_id,
                status=heartbeat.status,
                metrics=common_pb2.Metrics(
                    cpu=heartbeat.metrics.cpu,
                    memory=heartbeat.metrics.memory,
                    disk=heartbeat.metrics.disk,
                    running_tasks=heartbeat.metrics.running_tasks,
                    max_concurrent_tasks=heartbeat.metrics.max_concurrent_tasks,
                    task_count=heartbeat.metrics.task_count,
                ),
                os_info=common_pb2.OSInfo(
                    os_type=heartbeat.os_info.os_type,
                    os_version=heartbeat.os_info.os_version,
                    python_version=heartbeat.os_info.python_version,
                    machine_arch=heartbeat.os_info.machine_arch,
                ),
                timestamp=common_pb2.Timestamp(
                    seconds=int(heartbeat.timestamp.timestamp()),
                    nanos=heartbeat.timestamp.microsecond * 1000,
                ),
                capabilities={str(k): str(v) for k, v in heartbeat.capabilities.items()},
            )
            
            message = pb2.NodeMessage(heartbeat=pb_heartbeat)
            
            # 放入发送队列
            await self._send_queue.put(message)
            
            # 记录延迟
            latency = (time.time() - start_time) * 1000
            self._metrics.record_latency(latency)
            
            logger.debug(f"心跳消息已入队: node_id={heartbeat.node_id}")
            return True
            
        except Exception as e:
            logger.error(f"发送心跳失败: {e}")
            return False

    async def send_logs(self, logs: List[LogEntry]) -> bool:
        """
        发送日志批次
        
        Requirements: 3.1, 3.2, 3.3, 3.4
        """
        if not self.is_connected or not logs:
            return False
        
        try:
            # 构建日志条目
            pb_logs = []
            for log in logs:
                pb_log = pb2.LogEntry(
                    execution_id=log.execution_id,
                    log_type=log.log_type,
                    content=log.content,
                    timestamp=common_pb2.Timestamp(
                        seconds=int(log.timestamp.timestamp()),
                        nanos=log.timestamp.microsecond * 1000,
                    ),
                )
                pb_logs.append(pb_log)
            
            # 检查是否需要压缩
            total_size = sum(len(log.content.encode('utf-8')) for log in logs)
            
            if total_size > COMPRESS_THRESHOLD:
                # 压缩日志
                compressed_data = self._compress_logs(logs)
                log_batch = pb2.LogBatch(
                    compressed=True,
                    compressed_data=compressed_data,
                )
            else:
                log_batch = pb2.LogBatch(
                    logs=pb_logs,
                    compressed=False,
                )
            
            message = pb2.NodeMessage(log_batch=log_batch)
            
            # 放入发送队列
            await self._send_queue.put(message)
            
            logger.debug(f"日志批次已入队: count={len(logs)}, compressed={total_size > COMPRESS_THRESHOLD}")
            return True
            
        except Exception as e:
            logger.error(f"发送日志失败: {e}")
            return False

    def _compress_logs(self, logs: List[LogEntry]) -> bytes:
        """压缩日志数据"""
        import json
        log_dicts = [log.to_dict() for log in logs]
        json_data = json.dumps(log_dicts, separators=(',', ':'))
        return gzip.compress(json_data.encode('utf-8'))

    async def send_task_status(self, status: TaskStatus) -> bool:
        """
        发送任务状态更新
        
        Requirements: 4.1, 4.2
        """
        if not self.is_connected:
            return False
        
        try:
            pb_status = pb2.TaskStatus(
                execution_id=status.execution_id,
                status=status.status,
                timestamp=common_pb2.Timestamp(
                    seconds=int(status.timestamp.timestamp()),
                    nanos=status.timestamp.microsecond * 1000,
                ),
            )
            
            # 设置可选字段
            if status.exit_code is not None:
                pb_status.exit_code = status.exit_code
            if status.error_message is not None:
                pb_status.error_message = status.error_message
            
            message = pb2.NodeMessage(task_status=pb_status)
            
            # 放入发送队列
            await self._send_queue.put(message)
            
            logger.debug(f"任务状态已入队: execution_id={status.execution_id}, status={status.status}")
            return True
            
        except Exception as e:
            logger.error(f"发送任务状态失败: {e}")
            return False

    async def send_task_ack(self, task_id: str, accepted: bool, reason: Optional[str] = None) -> bool:
        """
        发送任务确认
        
        Requirements: 5.3
        """
        if not self.is_connected:
            return False
        
        try:
            pb_ack = pb2.TaskAck(
                task_id=task_id,
                accepted=accepted,
            )
            if reason is not None:
                pb_ack.reason = reason
            
            message = pb2.NodeMessage(task_ack=pb_ack)
            
            await self._send_queue.put(message)
            
            logger.debug(f"任务确认已入队: task_id={task_id}, accepted={accepted}")
            return True
            
        except Exception as e:
            logger.error(f"发送任务确认失败: {e}")
            return False

    async def send_cancel_ack(self, task_id: str, success: bool, reason: Optional[str] = None) -> bool:
        """
        发送取消确认
        
        Requirements: 6.3
        """
        if not self.is_connected:
            return False
        
        try:
            pb_ack = pb2.CancelAck(
                task_id=task_id,
                success=success,
            )
            if reason is not None:
                pb_ack.reason = reason
            
            message = pb2.NodeMessage(cancel_ack=pb_ack)
            
            await self._send_queue.put(message)
            
            logger.debug(f"取消确认已入队: task_id={task_id}, success={success}")
            return True
            
        except Exception as e:
            logger.error(f"发送取消确认失败: {e}")
            return False

    def on_task_dispatch(self, callback: Callable[[TaskDispatch], Awaitable[None]]):
        """注册任务分发回调"""
        self._on_task_dispatch = callback

    def on_task_cancel(self, callback: Callable[[TaskCancel], Awaitable[None]]):
        """注册任务取消回调"""
        self._on_task_cancel = callback

