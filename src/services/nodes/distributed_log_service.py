"""
分布式任务日志服务（优化版）

优化点：
1. 批量写入文件 - 减少 I/O 次数
2. 异步文件写入 - 不阻塞主流程
3. 缓存 WebSocket 连接状态 - 减少频繁检查
4. 写入缓冲区 - 积累一定量后批量写入
"""
import asyncio
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import defaultdict
from dataclasses import dataclass, field

from loguru import logger

from src.core.config import settings
from src.utils.serialization import json_dump_file, json_load_file


@dataclass
class LogBuffer:
    """日志缓冲区"""
    lines: List[str] = field(default_factory=list)
    last_flush: float = 0
    dirty: bool = False


class DistributedLogService:
    """分布式任务日志服务（优化版）"""

    # 缓冲区配置
    BUFFER_SIZE = 50  # 积累多少行后写入
    FLUSH_INTERVAL = 2.0  # 最大刷新间隔（秒）

    def __init__(self):
        # 内存缓存（用于实时查询和推送）
        self._log_cache: Dict[str, List[str]] = defaultdict(list)
        self._task_status: Dict[str, Dict[str, Any]] = {}

        # 写入缓冲区（按 execution_id:log_type 分组）
        self._write_buffers: Dict[str, LogBuffer] = defaultdict(LogBuffer)

        # 日志文件根目录
        self._log_root = os.path.join(settings.data_dir, "logs", "distributed")
        os.makedirs(self._log_root, exist_ok=True)

        # 最大缓存行数（每个任务）
        self._max_cache_lines = 1000

        # 锁
        self._buffer_lock = asyncio.Lock()
        self._file_locks: Dict[str, asyncio.Lock] = {}

        # WebSocket 连接缓存（减少频繁检查）
        self._ws_connection_cache: Dict[str, bool] = {}
        self._ws_cache_time: Dict[str, float] = {}
        self._ws_cache_ttl = 1.0  # 缓存 1 秒

        # 后台刷新任务
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        """启动服务"""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.debug("分布式日志服务已启动")

    async def stop(self):
        """停止服务"""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # 最终刷新
        await self._flush_all_buffers()
        logger.debug("分布式日志服务已停止")

    async def _flush_loop(self):
        """后台刷新循环"""
        while self._running:
            try:
                await asyncio.sleep(self.FLUSH_INTERVAL / 2)
                await self._flush_expired_buffers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"日志刷新循环异常: {e}")

    async def _flush_expired_buffers(self):
        """刷新过期的缓冲区"""
        now = asyncio.get_event_loop().time()
        to_flush = []

        async with self._buffer_lock:
            for key, buffer in self._write_buffers.items():
                if buffer.dirty and (now - buffer.last_flush) >= self.FLUSH_INTERVAL:
                    to_flush.append(key)

        for key in to_flush:
            await self._flush_buffer(key)

    async def _flush_all_buffers(self):
        """刷新所有缓冲区"""
        async with self._buffer_lock:
            keys = list(self._write_buffers.keys())

        for key in keys:
            await self._flush_buffer(key)

    async def _flush_buffer(self, buffer_key: str):
        """刷新指定缓冲区到文件"""
        async with self._buffer_lock:
            buffer = self._write_buffers.get(buffer_key)
            if not buffer or not buffer.lines:
                return

            lines_to_write = buffer.lines.copy()
            buffer.lines.clear()
            buffer.dirty = False
            buffer.last_flush = asyncio.get_event_loop().time()

        # 解析 key
        parts = buffer_key.rsplit(":", 1)
        if len(parts) != 2:
            return
        execution_id, log_type = parts

        # 异步写入文件
        await self._write_to_file(execution_id, log_type, lines_to_write)

    def _get_file_lock(self, execution_id: str) -> asyncio.Lock:
        """获取文件锁"""
        if execution_id not in self._file_locks:
            self._file_locks[execution_id] = asyncio.Lock()
        return self._file_locks[execution_id]

    async def _write_to_file(self, execution_id: str, log_type: str, lines: List[str]):
        """异步写入文件"""
        if not lines:
            return

        log_dir = self._get_log_dir(execution_id, create=True)
        filename = "stdout.log" if log_type == "stdout" else "stderr.log"
        log_file = os.path.join(log_dir, filename)

        content = "\n".join(lines) + "\n"

        lock = self._get_file_lock(execution_id)
        async with lock:
            try:
                # 使用线程池执行文件 I/O
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._sync_write_file, log_file, content)
            except Exception as e:
                logger.error(f"写入分布式日志失败: {e}")

    @staticmethod
    def _sync_write_file(file_path: str, content: str):
        """同步写入文件（在线程池中执行）"""
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(content)

    def _get_log_dir(self, execution_id: str, create: bool = True) -> str:
        """获取日志目录"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_dir = os.path.join(self._log_root, date_str, execution_id)
        if create:
            os.makedirs(log_dir, exist_ok=True)
        return log_dir

    def _find_log_dir(self, execution_id: str) -> Optional[str]:
        """查找已存在的日志目录"""
        if not os.path.exists(self._log_root):
            return None

        try:
            for date_dir in os.listdir(self._log_root):
                date_path = os.path.join(self._log_root, date_dir)
                if os.path.isdir(date_path):
                    execution_path = os.path.join(date_path, execution_id)
                    if os.path.exists(execution_path):
                        return execution_path
        except OSError:
            pass
        return None

    def _get_log_file(self, execution_id: str, log_type: str) -> str:
        """获取日志文件路径"""
        existing_dir = self._find_log_dir(execution_id)
        if existing_dir:
            filename = "stdout.log" if log_type == "stdout" else "stderr.log"
            return os.path.join(existing_dir, filename)

        log_dir = self._get_log_dir(execution_id, create=False)
        filename = "stdout.log" if log_type == "stdout" else "stderr.log"
        return os.path.join(log_dir, filename)

    def _has_ws_connections(self, execution_id: str) -> bool:
        """检查是否有 WebSocket 连接（带缓存）"""
        now = asyncio.get_event_loop().time()

        # 检查缓存
        if execution_id in self._ws_cache_time:
            if (now - self._ws_cache_time[execution_id]) < self._ws_cache_ttl:
                return self._ws_connection_cache.get(execution_id, False)

        # 查询并缓存
        try:
            from src.services.websockets.websocket_connection_manager import websocket_manager
            has_conn = websocket_manager.get_connections_for_execution(execution_id) > 0
            self._ws_connection_cache[execution_id] = has_conn
            self._ws_cache_time[execution_id] = now
            return has_conn
        except Exception:
            return False

    async def append_log(
        self,
        execution_id: str,
        log_type: str,
        content: str,
        machine_code: Optional[str] = None,
    ):
        """
        追加日志行（优化版：批量写入 + 异步推送）
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] {content}"

        buffer_key = f"{execution_id}:{log_type}"
        should_flush = False

        async with self._buffer_lock:
            # 添加到写入缓冲区
            buffer = self._write_buffers[buffer_key]
            buffer.lines.append(log_line)
            buffer.dirty = True
            if not buffer.last_flush:
                buffer.last_flush = asyncio.get_event_loop().time()

            # 检查是否需要立即刷新
            if len(buffer.lines) >= self.BUFFER_SIZE:
                should_flush = True

            # 更新内存缓存（用于实时查询）
            self._log_cache[buffer_key].append(log_line)
            if len(self._log_cache[buffer_key]) > self._max_cache_lines:
                self._log_cache[buffer_key] = self._log_cache[buffer_key][-self._max_cache_lines:]

        # 缓冲区满时立即刷新
        if should_flush:
            asyncio.create_task(self._flush_buffer(buffer_key))

        # 实时推送到 WebSocket（如果有连接）
        if self._has_ws_connections(execution_id):
            asyncio.create_task(self._push_to_websocket(execution_id, log_type, log_line))

    async def _push_to_websocket(
        self,
        execution_id: str,
        log_type: str,
        content: str
    ):
        """推送日志到 WebSocket"""
        try:
            from src.services.websockets.websocket_connection_manager import websocket_manager
            level = "ERROR" if log_type == "stderr" else "INFO"
            await websocket_manager.send_log_message(execution_id, log_type, content, level)
        except Exception as e:
            logger.debug(f"推送日志到 WebSocket 失败: {e}")

    async def update_task_status(
        self,
        execution_id: str,
        status: str,
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
        machine_code: Optional[str] = None,
    ):
        """更新任务状态"""
        self._task_status[execution_id] = {
            "status": status,
            "exit_code": exit_code,
            "error_message": error_message,
            "updated_at": datetime.now().isoformat(),
            "machine_code": machine_code,
        }

        # 刷新该任务的日志缓冲区（确保日志先于状态到达）
        for log_type in ["stdout", "stderr"]:
            await self._flush_buffer(f"{execution_id}:{log_type}")

        # 写入状态文件
        log_dir = self._get_log_dir(execution_id)
        status_file = os.path.join(log_dir, "status.json")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                lambda: json_dump_file(self._task_status[execution_id], status_file)
            )
        except Exception as e:
            logger.error(f"写入任务状态失败: {e}")

        # 记录状态变更到日志
        status_msg = f"[STATUS] 任务状态更新: {status}"
        if exit_code is not None:
            status_msg += f", 退出码: {exit_code}"
        if error_message:
            status_msg += f", 错误: {error_message}"

        await self.append_log(execution_id, "stdout", status_msg, machine_code)

        # 更新数据库
        await self._update_execution_record(execution_id, status, exit_code, error_message)

        logger.info(f"分布式任务状态更新: {execution_id} -> {status}")

    async def _update_execution_record(
        self,
        execution_id: str,
        status: str,
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ):
        """更新数据库中的执行记录"""
        try:
            from src.models.scheduler import TaskExecution, ScheduledTask
            from src.models.enums import TaskStatus

            execution = await TaskExecution.get_or_none(execution_id=execution_id)
            if not execution:
                logger.warning(f"执行记录不存在: {execution_id}")
                return

            status_map = {
                "running": TaskStatus.RUNNING,
                "success": TaskStatus.SUCCESS,
                "completed": TaskStatus.SUCCESS,
                "failed": TaskStatus.FAILED,
                "error": TaskStatus.FAILED,
                "timeout": TaskStatus.TIMEOUT,
                "cancelled": TaskStatus.CANCELLED,
            }

            new_status = status_map.get(status.lower())
            if not new_status:
                logger.warning(f"未知状态: {status}")
                return

            execution.status = new_status
            if exit_code is not None:
                execution.exit_code = exit_code
            if error_message:
                execution.error_message = error_message

            if new_status in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
                from datetime import timezone
                now = datetime.now(timezone.utc)
                execution.end_time = now
                if execution.start_time:
                    start_time = execution.start_time
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=timezone.utc)
                    execution.duration_seconds = (now - start_time).total_seconds()

            await execution.save()

            task_name = None
            task = await ScheduledTask.get_or_none(id=execution.task_id)
            if task:
                task_name = task.name
                if new_status == TaskStatus.RUNNING:
                    task.status = TaskStatus.RUNNING
                elif new_status == TaskStatus.SUCCESS:
                    task.status = TaskStatus.SUCCESS
                    task.success_count = (task.success_count or 0) + 1
                elif new_status in (TaskStatus.FAILED, TaskStatus.TIMEOUT):
                    task.status = new_status
                    task.failure_count = (task.failure_count or 0) + 1
                await task.save()

            logger.info(f"执行记录已更新: {execution_id} -> {new_status}")

            await self._push_status_to_frontend(
                execution_id=execution_id,
                status=new_status.value,
                task_name=task_name,
                exit_code=exit_code,
                error_message=error_message,
                duration_seconds=execution.duration_seconds,
            )

        except Exception as e:
            logger.error(f"更新执行记录失败: {e}")

    async def _push_status_to_frontend(
        self,
        execution_id: str,
        status: str,
        task_name: str = None,
        exit_code: int = None,
        error_message: str = None,
        duration_seconds: float = None,
    ):
        """推送任务状态到前端"""
        try:
            from src.services.websockets.websocket_connection_manager import websocket_manager

            message = f"任务状态: {status}"
            if status == "RUNNING":
                message = "任务开始执行"
            elif status == "SUCCESS":
                message = "任务执行成功"
            elif status == "FAILED":
                message = f"任务执行失败: {error_message or '未知错误'}"
            elif status == "TIMEOUT":
                message = "任务执行超时"
            elif status == "CANCELLED":
                message = "任务已取消"

            await websocket_manager.send_execution_status(
                execution_id=execution_id,
                status=status,
                progress=100.0 if status in ("SUCCESS", "FAILED", "TIMEOUT", "CANCELLED") else None,
                message=message,
            )
        except Exception as e:
            logger.warning(f"推送状态到前端失败: {e}")

    async def get_logs(
        self,
        execution_id: str,
        log_type: str = "stdout",
        tail: int = 100,
    ) -> List[str]:
        """获取日志"""
        cache_key = f"{execution_id}:{log_type}"

        # 优先从缓存获取
        if cache_key in self._log_cache:
            return self._log_cache[cache_key][-tail:]

        # 从文件读取
        log_file = self._get_log_file(execution_id, log_type)
        if not os.path.exists(log_file):
            return []

        try:
            loop = asyncio.get_event_loop()
            lines = await loop.run_in_executor(None, self._sync_read_file, log_file, tail)
            return lines
        except Exception as e:
            logger.error(f"读取分布式日志失败: {e}")
            return []

    @staticmethod
    def _sync_read_file(file_path: str, tail: int) -> List[str]:
        """同步读取文件（在线程池中执行）"""
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return [line.rstrip() for line in lines[-tail:]]

    async def get_task_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        if execution_id in self._task_status:
            return self._task_status[execution_id]

        log_dir = self._find_log_dir(execution_id)
        if not log_dir:
            return None

        status_file = os.path.join(log_dir, "status.json")
        if os.path.exists(status_file):
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(
                    None,
                    lambda: json_load_file(status_file)
                )
            except Exception:
                pass
        return None

    async def get_all_logs(self, execution_id: str) -> Dict[str, List[str]]:
        """获取所有类型的日志"""
        return {
            "stdout": await self.get_logs(execution_id, "stdout", 5000),
            "stderr": await self.get_logs(execution_id, "stderr", 5000),
        }

    def clear_cache(self, execution_id: str):
        """清理指定执行ID的缓存"""
        for log_type in ["stdout", "stderr"]:
            cache_key = f"{execution_id}:{log_type}"
            if cache_key in self._log_cache:
                del self._log_cache[cache_key]
            if cache_key in self._write_buffers:
                del self._write_buffers[cache_key]

        if execution_id in self._task_status:
            del self._task_status[execution_id]
        if execution_id in self._file_locks:
            del self._file_locks[execution_id]
        if execution_id in self._ws_connection_cache:
            del self._ws_connection_cache[execution_id]
        if execution_id in self._ws_cache_time:
            del self._ws_cache_time[execution_id]

    async def cleanup_old_logs(self, days: int = 7):
        """清理旧日志"""
        import shutil
        from datetime import timedelta

        now = datetime.now()
        cutoff = now - timedelta(days=days)

        cleaned = 0
        try:
            for date_dir in os.listdir(self._log_root):
                try:
                    dir_date = datetime.strptime(date_dir, "%Y-%m-%d")
                    if dir_date < cutoff:
                        dir_path = os.path.join(self._log_root, date_dir)
                        shutil.rmtree(dir_path)
                        cleaned += 1
                except (ValueError, OSError):
                    continue
        except OSError:
            pass

        if cleaned:
            logger.info(f"清理了 {cleaned} 个旧的分布式日志目录")


# 全局实例
distributed_log_service = DistributedLogService()
