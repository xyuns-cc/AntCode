"""
实时日志处理器 - 展示通道

实现日志传输双通道架构中的 Master 端展示通道处理器。
负责处理实时日志消息转发到 WebSocket，以及控制 Worker 的实时模式。

Requirements: 3.2
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import aiofiles
from loguru import logger

from antcode_contracts.worker_pb2 import (
    RealtimeModeControl,
    WorkerMasterMessage,
)
from antcode_core.application.services.logs.log_chunk_receiver import log_chunk_receiver


class LogRealtimeHandler:
    """
    实时日志处理器 - 展示通道
    
    负责处理实时日志消息转发到 WebSocket，以及控制 Worker 的实时模式。
    
    特性：
    - 实时日志转发：将 Worker 发送的实时日志转发到 WebSocket
    - 实时模式控制：发送 RealtimeModeControl 消息到 Worker
    - 引用计数：多个前端连接订阅同一 execution_id 时，保持实时模式开启
    - 历史日志推送：WebSocket 连接时先推送历史日志
    
    Requirements: 3.2
    """
    
    # 历史日志推送的分片大小
    HISTORY_CHUNK_SIZE = 8192  # 8KB
    
    def __init__(self):
        """初始化实时日志处理器"""
        # 活跃的 execution_id -> (ref_count, worker_id)
        self._active_executions: dict[str, tuple[int, Optional[str]]] = {}
        
        # 锁
        self._lock = asyncio.Lock()
        
        # execution_id -> worker_id 映射（用于查找 Worker）
        self._execution_worker_map: dict[str, str] = {}
    
    # ==================== 公共接口 ====================
    
    async def handle_realtime_log(
        self,
        execution_id: str,
        log_type: str,
        content: str,
        timestamp: int,
    ) -> None:
        """
        处理实时日志消息，转发到 WebSocket
        
        从 gRPC 流接收实时日志消息，转发到订阅该 execution_id 的 WebSocket 连接。
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            content: 日志内容
            timestamp: 时间戳（毫秒）
            
        Requirements: 3.2
        """
        try:
            from antcode_core.application.services.websockets.websocket_connection_manager import (
                websocket_manager,
            )
            
            # 检查是否有活跃的 WebSocket 连接
            conn_count = websocket_manager.get_connections_for_execution(execution_id)
            if conn_count == 0:
                logger.debug(
                    f"[{execution_id}] 无活跃 WebSocket 连接，忽略实时日志"
                )
                return
            
            # 确定日志级别
            level = "ERROR" if log_type == "stderr" else "INFO"
            
            # 转发到 WebSocket
            await websocket_manager.send_log_message(
                execution_id=execution_id,
                log_type=log_type,
                content=content,
                level=level,
                source="worker_realtime",
            )
            
            logger.debug(
                f"[{execution_id}/{log_type}] 实时日志已转发: "
                f"content_len={len(content)}, connections={conn_count}"
            )
            
        except Exception as e:
            logger.error(
                f"[{execution_id}] 处理实时日志失败: {e}"
            )
    
    async def request_realtime_mode(
        self,
        execution_id: str,
        enabled: bool,
        worker_id: Optional[str] = None,
    ) -> bool:
        """
        请求 Worker 开启/关闭实时模式
        
        通过 worker_connector.send_to_worker() 发送 RealtimeModeControl 消息，
        复用现有的单一 gRPC 双向流。
        
        Args:
            execution_id: 任务执行 ID
            enabled: 是否开启实时模式
            worker_id: Worker 节点 ID（可选，如果不提供则从映射中查找）
            
        Returns:
            是否成功发送控制消息
            
        Requirements: 3.2
        """
        try:
            # 获取 worker_id
            if not worker_id:
                worker_id = await self._get_worker_id_for_execution(execution_id)
            
            if not worker_id:
                logger.warning(
                    f"[{execution_id}] 无法发送实时模式控制: 未找到 worker_id"
                )
                return False
            
            # 获取 worker_client
            from antcode_core.application.services.grpc.worker_connector import get_worker_client
            
            worker_client = get_worker_client()
            
            # 构建控制消息
            control_msg = RealtimeModeControl(
                execution_id=execution_id,
                enabled=enabled,
            )
            
            # 包装为 WorkerMasterMessage
            message = WorkerMasterMessage(realtime_mode=control_msg)
            
            # 发送到 Worker
            success = await worker_client.send_to_worker(worker_id, message)
            
            if success:
                logger.info(
                    f"[{execution_id}] 实时模式控制已发送: "
                    f"enabled={enabled}, worker_id={worker_id}"
                )
            else:
                logger.warning(
                    f"[{execution_id}] 实时模式控制发送失败: worker_id={worker_id}"
                )
            
            return success
            
        except Exception as e:
            logger.error(
                f"[{execution_id}] 发送实时模式控制失败: {e}"
            )
            return False
    
    async def on_websocket_connect(
        self,
        execution_id: str,
        worker_id: Optional[str] = None,
    ) -> None:
        """
        WebSocket 连接时开启实时模式
        
        1) 从 Master 本地存储读取历史日志（从 0 到最新）并推送给前端
        2) 历史日志推送完成后开启 Worker 实时推送新日志
        
        Args:
            execution_id: 任务执行 ID
            worker_id: Worker 节点 ID（可选）
            
        Requirements: 3.2
        """
        try:
            async with self._lock:
                # 更新引用计数
                if execution_id in self._active_executions:
                    ref_count, stored_worker_id = self._active_executions[execution_id]
                    self._active_executions[execution_id] = (
                        ref_count + 1,
                        worker_id or stored_worker_id,
                    )
                    logger.debug(
                        f"[{execution_id}] WebSocket 连接增加: "
                        f"ref_count={ref_count + 1}"
                    )
                    # 已有连接，不需要再次开启实时模式
                    return
                else:
                    # 第一个连接
                    self._active_executions[execution_id] = (1, worker_id)
                    logger.info(
                        f"[{execution_id}] 首个 WebSocket 连接，准备开启实时模式"
                    )
            
            # 保存 execution_id -> worker_id 映射
            if worker_id:
                self._execution_worker_map[execution_id] = worker_id
            
            # 1. 推送历史日志
            await self._push_historical_logs(execution_id)
            
            # 2. 开启 Worker 实时推送
            await self.request_realtime_mode(execution_id, enabled=True, worker_id=worker_id)
            
        except Exception as e:
            logger.error(
                f"[{execution_id}] WebSocket 连接处理失败: {e}"
            )
    
    async def on_websocket_disconnect(
        self,
        execution_id: str,
    ) -> None:
        """
        WebSocket 断开时关闭实时模式（仅在 ref_count=0 时关闭）
        
        Args:
            execution_id: 任务执行 ID
            
        Requirements: 3.2
        """
        try:
            async with self._lock:
                if execution_id not in self._active_executions:
                    logger.debug(
                        f"[{execution_id}] WebSocket 断开: 无活跃记录"
                    )
                    return
                
                ref_count, worker_id = self._active_executions[execution_id]
                
                if ref_count > 1:
                    # 还有其他连接
                    self._active_executions[execution_id] = (ref_count - 1, worker_id)
                    logger.debug(
                        f"[{execution_id}] WebSocket 连接减少: "
                        f"ref_count={ref_count - 1}"
                    )
                    return
                else:
                    # 最后一个连接断开
                    del self._active_executions[execution_id]
                    logger.info(
                        f"[{execution_id}] 最后一个 WebSocket 连接断开，关闭实时模式"
                    )
            
            # 关闭 Worker 实时推送
            await self.request_realtime_mode(execution_id, enabled=False, worker_id=worker_id)
            
            # 清理映射
            self._execution_worker_map.pop(execution_id, None)
            
        except Exception as e:
            logger.error(
                f"[{execution_id}] WebSocket 断开处理失败: {e}"
            )
    
    def register_execution_worker(
        self,
        execution_id: str,
        worker_id: str,
    ) -> None:
        """
        注册 execution_id 到 worker_id 的映射
        
        在任务分发时调用，用于后续查找 Worker。
        
        Args:
            execution_id: 任务执行 ID
            worker_id: Worker 节点 ID
        """
        self._execution_worker_map[execution_id] = worker_id
        logger.debug(
            f"[{execution_id}] 注册 worker_id 映射: {worker_id}"
        )
    
    def unregister_execution_worker(
        self,
        execution_id: str,
    ) -> None:
        """
        取消注册 execution_id 到 worker_id 的映射
        
        在任务完成时调用。
        
        Args:
            execution_id: 任务执行 ID
        """
        self._execution_worker_map.pop(execution_id, None)
        logger.debug(
            f"[{execution_id}] 取消 worker_id 映射"
        )
    
    def get_active_execution_count(self) -> int:
        """获取活跃的 execution 数量"""
        return len(self._active_executions)
    
    def get_total_connection_count(self) -> int:
        """获取总的 WebSocket 连接数（所有 execution 的引用计数之和）"""
        return sum(ref_count for ref_count, _ in self._active_executions.values())
    
    def is_realtime_enabled(self, execution_id: str) -> bool:
        """检查指定 execution_id 是否开启了实时模式"""
        return execution_id in self._active_executions
    
    # ==================== 内部方法 ====================
    
    async def _get_worker_id_for_execution(
        self,
        execution_id: str,
    ) -> Optional[str]:
        """
        获取 execution_id 对应的 worker_id
        
        优先从内存映射中查找，如果没有则从数据库查询。
        
        Args:
            execution_id: 任务执行 ID
            
        Returns:
            worker_id 或 None
        """
        # 1. 从内存映射查找
        if execution_id in self._execution_worker_map:
            return self._execution_worker_map[execution_id]
        
        # 2. 从活跃执行中查找
        if execution_id in self._active_executions:
            _, worker_id = self._active_executions[execution_id]
            if worker_id:
                return worker_id
        
        # 3. 从数据库查询
        try:
            from antcode_core.domain.models import TaskRun, Worker

            execution = await TaskRun.get_or_none(execution_id=execution_id)
            if execution and execution.worker_id:
                # 获取 Worker 的 public_id
                worker = await Worker.get_or_none(id=execution.worker_id)
                if worker:
                    # 缓存映射
                    self._execution_worker_map[execution_id] = worker.public_id
                    return worker.public_id
        except Exception as e:
            logger.warning(
                f"[{execution_id}] 从数据库查询 worker_id 失败: {e}"
            )
        
        return None
    
    async def _push_historical_logs(
        self,
        execution_id: str,
    ) -> None:
        """
        推送历史日志到 WebSocket
        
        从 Master 本地存储读取历史日志（从 offset 0 到最新），
        并推送给前端。
        
        Args:
            execution_id: 任务执行 ID
            
        Requirements: 3.2
        """
        try:
            from antcode_core.application.services.websockets.websocket_connection_manager import (
                websocket_manager,
            )
            
            # 发送历史日志开始标记
            await websocket_manager.send_historical_logs_start(execution_id)
            
            sent_lines = 0
            
            # 推送 stdout 历史日志
            stdout_lines = await self._read_log_file(execution_id, "stdout")
            for line in stdout_lines:
                await self._wait_for_queue_space(execution_id, websocket_manager)
                await websocket_manager.send_log_message(
                    execution_id=execution_id,
                    log_type="stdout",
                    content=line,
                    level="INFO",
                    source="historical",
                )
                sent_lines += 1
                # 每 100 行让出控制权
                if sent_lines % 100 == 0:
                    await asyncio.sleep(0.01)
            
            # 推送 stderr 历史日志
            stderr_lines = await self._read_log_file(execution_id, "stderr")
            for line in stderr_lines:
                await self._wait_for_queue_space(execution_id, websocket_manager)
                await websocket_manager.send_log_message(
                    execution_id=execution_id,
                    log_type="stderr",
                    content=line,
                    level="ERROR",
                    source="historical",
                )
                sent_lines += 1
                if sent_lines % 100 == 0:
                    await asyncio.sleep(0.01)
            
            # 发送历史日志结束标记
            if sent_lines > 0:
                await websocket_manager.send_historical_logs_end(execution_id, sent_lines)
                logger.info(
                    f"[{execution_id}] 历史日志推送完成: {sent_lines} 行"
                )
            else:
                await websocket_manager.send_no_historical_logs(execution_id)
                logger.debug(
                    f"[{execution_id}] 无历史日志"
                )
            
        except Exception as e:
            logger.error(
                f"[{execution_id}] 推送历史日志失败: {e}"
            )
            # 发送无历史日志标记
            try:
                from antcode_core.application.services.websockets.websocket_connection_manager import (
                    websocket_manager,
                )
                await websocket_manager.send_no_historical_logs(execution_id)
            except Exception:
                pass
    
    async def _read_log_file(
        self,
        execution_id: str,
        log_type: str,
    ) -> list[str]:
        """
        读取日志文件内容
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            
        Returns:
            日志行列表
        """
        try:
            # 获取日志文件路径
            log_file = log_chunk_receiver.get_log_file_path(execution_id, log_type)
            
            if not log_file.exists():
                return []
            
            # 异步读取文件
            async with aiofiles.open(log_file, "r", encoding="utf-8", errors="replace") as f:
                content = await f.read()
            
            if not content:
                return []

            lines = content.splitlines()
            if content.endswith(("\n", "\r")):
                lines.append("")
            return lines
            
        except Exception as e:
            logger.warning(
                f"[{execution_id}/{log_type}] 读取日志文件失败: {e}"
            )
            return []

    async def _wait_for_queue_space(
        self,
        execution_id: str,
        websocket_manager,
    ) -> None:
        """
        等待消息队列有空间，避免历史日志被丢弃
        """
        queue = websocket_manager.message_queue
        max_size = queue.max_queue_size
        if max_size <= 0:
            return

        while queue.get_queue_size(execution_id) >= max_size - 50:
            await asyncio.sleep(0.05)


# 全局实例
log_realtime_handler = LogRealtimeHandler()


def get_log_realtime_handler() -> LogRealtimeHandler:
    """获取全局 LogRealtimeHandler 实例"""
    return log_realtime_handler
