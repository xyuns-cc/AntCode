"""
分布式任务日志服务（优化版）

优化点：
1. 批量写入文件 - 减少 I/O 次数
2. 异步文件写入 - 不阻塞主流程
3. 缓存 WebSocket 连接状态 - 减少频繁检查
4. 写入缓冲区 - 积累一定量后批量写入
"""

import asyncio
import contextlib
import os
from collections import deque
from collections import defaultdict
from datetime import datetime

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.serialization import json_dump_file, json_load_file
from antcode_core.application.services.workers.log_notifier import LogRealtimeNotifier


class LogBuffer:
    """日志缓冲区"""

    def __init__(self):
        self.lines = []
        self.last_flush = 0
        self.dirty = False


class DistributedLogService:
    """分布式任务日志服务（优化版）"""

    # 缓冲区配置
    BUFFER_SIZE = 50  # 积累多少行后写入
    FLUSH_INTERVAL = 2.0  # 最大刷新间隔（秒）

    def __init__(self):
        # 内存缓存（用于实时查询和推送）
        self._log_cache = defaultdict(list)
        self._task_status = {}

        # 写入缓冲区（按 run_id:log_type 分组）
        self._write_buffers = defaultdict(LogBuffer)
        self._flush_tasks = {}

        # 日志文件根目录
        self._log_root = os.path.join(settings.data_dir, "logs", "distributed")
        os.makedirs(self._log_root, exist_ok=True)

        # 最大缓存行数（每个任务）
        self._max_cache_lines = 1000

        # 锁
        self._buffer_lock = asyncio.Lock()
        self._file_locks = {}

        # WebSocket 连接缓存（减少频繁检查）
        self._ws_connection_cache = {}
        self._ws_cache_time = {}
        self._ws_cache_ttl = 1.0  # 缓存 1 秒
        self._ws_queues = {}
        self._ws_tasks = {}
        self._ws_idle_timeout = 1.0

        self._notifier: LogRealtimeNotifier | None = None

        # 后台刷新任务
        self._flush_task = None
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
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
        for task in self._ws_tasks.values():
            task.cancel()
        self._ws_tasks.clear()
        self._ws_queues.clear()
        # 最终刷新
        await self._flush_all_buffers()
        logger.debug("分布式日志服务已停止")

    def set_notifier(self, notifier: LogRealtimeNotifier | None) -> None:
        """设置日志实时推送通知器。"""
        self._notifier = notifier

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
            await self._ensure_flush_task(key)

    async def _flush_all_buffers(self):
        """刷新所有缓冲区"""
        async with self._buffer_lock:
            keys = list(self._write_buffers.keys())

        for key in keys:
            await self._ensure_flush_task(key)

        await self._drain_flush_tasks()

    async def _ensure_flush_task(self, buffer_key):
        async with self._buffer_lock:
            if buffer_key in self._flush_tasks:
                return
            task = asyncio.create_task(self._flush_buffer_loop(buffer_key))
            self._flush_tasks[buffer_key] = task

    async def _drain_flush_tasks(self):
        async with self._buffer_lock:
            tasks = list(self._flush_tasks.values())

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _flush_buffer_now(self, buffer_key):
        await self._ensure_flush_task(buffer_key)
        task = self._flush_tasks.get(buffer_key)
        if task:
            await task

    async def _flush_buffer_loop(self, buffer_key):
        """刷新指定缓冲区到文件（严格顺序）"""
        try:
            while True:
                async with self._buffer_lock:
                    buffer = self._write_buffers.get(buffer_key)
                    if not buffer or not buffer.lines:
                        self._flush_tasks.pop(buffer_key, None)
                        return

                    lines_to_write = buffer.lines.copy()
                    buffer.lines.clear()
                    buffer.dirty = False
                    buffer.last_flush = asyncio.get_event_loop().time()

                parts = buffer_key.rsplit(":", 1)
                if len(parts) != 2:
                    return
                run_id, log_type = parts

                await self._write_to_file(run_id, log_type, lines_to_write)
        finally:
            async with self._buffer_lock:
                self._flush_tasks.pop(buffer_key, None)

    def _get_file_lock(self, run_id):
        """获取文件锁"""
        if run_id not in self._file_locks:
            self._file_locks[run_id] = asyncio.Lock()
        return self._file_locks[run_id]

    async def _write_to_file(self, run_id, log_type, lines):
        """异步写入文件"""
        if not lines:
            return

        log_dir = self._get_log_dir(run_id, create=True)
        filename = "stdout.log" if log_type == "stdout" else "stderr.log"
        log_file = os.path.join(log_dir, filename)

        content = "\n".join(lines) + "\n"

        lock = self._get_file_lock(run_id)
        async with lock:
            try:
                # 使用线程池执行文件 I/O
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._sync_write_file, log_file, content)
            except Exception as e:
                logger.error(f"写入分布式日志失败: {e}")

    @staticmethod
    def _sync_write_file(file_path, content):
        """同步写入文件（在线程池中执行）"""
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(content)

    def _get_log_dir(self, run_id, create=True):
        """获取日志目录"""
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_dir = os.path.join(self._log_root, date_str, run_id)
        if create:
            os.makedirs(log_dir, exist_ok=True)
        return log_dir

    def _find_log_dir(self, run_id):
        """查找已存在的日志目录"""
        if not os.path.exists(self._log_root):
            return None

        try:
            for date_dir in os.listdir(self._log_root):
                date_path = os.path.join(self._log_root, date_dir)
                if os.path.isdir(date_path):
                    execution_path = os.path.join(date_path, run_id)
                    if os.path.exists(execution_path):
                        return execution_path
        except OSError:
            pass
        return None

    def _get_log_file(self, run_id, log_type):
        """获取日志文件路径"""
        existing_dir = self._find_log_dir(run_id)
        if existing_dir:
            filename = "stdout.log" if log_type == "stdout" else "stderr.log"
            return os.path.join(existing_dir, filename)

        log_dir = self._get_log_dir(run_id, create=False)
        filename = "stdout.log" if log_type == "stdout" else "stderr.log"
        return os.path.join(log_dir, filename)

    async def _has_ws_connections(self, run_id):
        """检查是否有 WebSocket 连接（带缓存）"""
        if not self._notifier:
            return False

        now = asyncio.get_event_loop().time()

        # 检查缓存
        if run_id in self._ws_cache_time:
            if (now - self._ws_cache_time[run_id]) < self._ws_cache_ttl:
                return self._ws_connection_cache.get(run_id, False)

        # 查询并缓存
        try:
            has_conn = await self._notifier.has_connections(run_id)
            self._ws_connection_cache[run_id] = has_conn
            self._ws_cache_time[run_id] = now
            return has_conn
        except Exception:
            return False

    def _get_ws_queue(self, run_id):
        queue = self._ws_queues.get(run_id)
        if not queue:
            queue = asyncio.Queue()
            self._ws_queues[run_id] = queue
        return queue

    def _enqueue_ws_logs(self, run_id, log_type, log_lines):
        queue = self._get_ws_queue(run_id)
        for line in log_lines:
            queue.put_nowait((log_type, line))
        self._ensure_ws_task(run_id)

    def _ensure_ws_task(self, run_id):
        if run_id in self._ws_tasks:
            return
        task = asyncio.create_task(self._ws_loop(run_id))
        self._ws_tasks[run_id] = task

    async def _ws_loop(self, run_id):
        queue = self._ws_queues.get(run_id)
        if not queue:
            return
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=self._ws_idle_timeout,
                    )
                except TimeoutError:
                    if queue.empty():
                        break
                    continue
                log_type, content = item
                await self._push_to_websocket(run_id, log_type, content)
        finally:
            self._ws_tasks.pop(run_id, None)
            if queue and queue.empty():
                self._ws_queues.pop(run_id, None)

    async def append_log(
        self,
        run_id,
        log_type,
        content,
    ):
        """
        追加日志行（优化版：批量写入 + 异步推送）
        """
        await self.append_logs(
            run_id=run_id,
            log_type=log_type,
            contents=[content],
        )

    async def append_logs(
        self,
        run_id,
        log_type,
        contents,
    ):
        if not contents:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_lines = [f"[{timestamp}] {content}" for content in contents]

        buffer_key = f"{run_id}:{log_type}"
        should_flush = False

        async with self._buffer_lock:
            # 添加到写入缓冲区
            buffer = self._write_buffers[buffer_key]
            buffer.lines.extend(log_lines)
            buffer.dirty = True
            if not buffer.last_flush:
                buffer.last_flush = asyncio.get_event_loop().time()

            # 检查是否需要立即刷新
            if len(buffer.lines) >= self.BUFFER_SIZE:
                should_flush = True

            # 更新内存缓存（用于实时查询）
            cache = self._log_cache[buffer_key]
            cache.extend(log_lines)
            if len(cache) > self._max_cache_lines:
                self._log_cache[buffer_key] = cache[-self._max_cache_lines :]

        # 缓冲区满时立即刷新
        if should_flush:
            await self._ensure_flush_task(buffer_key)

        # 实时推送到 WebSocket（如果有连接）
        if await self._has_ws_connections(run_id):
            self._enqueue_ws_logs(run_id, log_type, log_lines)

    async def _push_to_websocket(self, run_id, log_type, content):
        """推送日志到 WebSocket"""
        if not self._notifier:
            return
        try:
            level = "ERROR" if log_type == "stderr" else "INFO"
            await self._notifier.send_log(run_id, log_type, content, level)
        except Exception as e:
            logger.debug(f"推送日志到 WebSocket 失败: {e}")

    async def update_task_status(
        self,
        run_id,
        status,
        exit_code=None,
        error_message=None,
        status_at=None,
    ):
        """更新任务状态"""
        if status_at is None:
            status_at = datetime.now().astimezone()

        self._task_status[run_id] = {
            "status": status,
            "exit_code": exit_code,
            "error_message": error_message,
            "updated_at": status_at.isoformat(),
        }

        # 刷新该任务的日志缓冲区（确保日志先于状态到达）
        for log_type in ["stdout", "stderr"]:
            await self._flush_buffer_now(f"{run_id}:{log_type}")

        # 写入状态文件
        log_dir = self._get_log_dir(run_id)
        status_file = os.path.join(log_dir, "status.json")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: json_dump_file(self._task_status[run_id], status_file),
            )
        except Exception as e:
            logger.error(f"写入任务状态失败: {e}")

        # 记录状态变更到日志
        status_msg = f"[STATUS] 任务状态更新: {status}"
        if exit_code is not None:
            status_msg += f", 退出码: {exit_code}"
        if error_message:
            status_msg += f", 错误: {error_message}"

        await self.append_log(run_id, "stdout", status_msg)

        from antcode_core.application.services.scheduler.execution_status_service import (
            execution_status_service,
        )

        await execution_status_service.update_runtime_status(
            run_id=run_id,
            status=status,
            status_at=status_at,
            exit_code=exit_code,
            error_message=error_message,
        )

        try:
            from antcode_core.domain.models.task import Task
            from antcode_core.domain.models.task_run import TaskRun

            execution = await TaskRun.get_or_none(run_id=run_id)
            if execution:
                task_name = None
                task = await Task.get_or_none(id=execution.task_id)
                if task:
                    task_name = task.name

                await self._push_status_to_frontend(
                    run_id=run_id,
                    status=execution.status.value,
                    task_name=task_name,
                    exit_code=execution.exit_code,
                    error_message=execution.error_message,
                    duration_seconds=execution.duration_seconds,
                )
        except Exception as e:
            logger.debug(f"推送状态到前端失败: {e}")

        logger.info(f"分布式任务状态更新: {run_id} -> {status}")

    async def _push_status_to_frontend(
        self,
        run_id,
        status,
        task_name=None,
        exit_code=None,
        error_message=None,
        duration_seconds=None,
    ):
        """推送任务状态到前端"""
        if not self._notifier:
            return
        try:
            status_value = status.lower() if isinstance(status, str) else str(status).lower()

            message = f"任务状态: {status_value}"
            if status_value == "running":
                message = "任务开始执行"
            elif status_value == "success":
                message = "任务执行成功"
            elif status_value == "failed":
                message = f"任务执行失败: {error_message or '未知错误'}"
            elif status_value == "timeout":
                message = "任务执行超时"
            elif status_value == "cancelled":
                message = "任务已取消"

            await self._notifier.send_status(
                run_id=run_id,
                status=status_value,
                progress=100.0
                if status_value in ("success", "failed", "timeout", "cancelled", "skipped", "rejected")
                else None,
                message=message,
            )
        except Exception as e:
            logger.warning(f"推送状态到前端失败: {e}")

    async def get_logs(
        self,
        run_id,
        log_type="stdout",
        tail=100,
    ):
        """获取日志"""
        cache_key = f"{run_id}:{log_type}"
        cache = self._log_cache.get(cache_key)
        if cache and tail is not None and len(cache) >= tail:
            return cache[-tail:]

        # 从文件读取
        log_file = self._get_log_file(run_id, log_type)
        if not os.path.exists(log_file):
            return cache[-tail:] if cache else []

        try:
            loop = asyncio.get_event_loop()
            lines = await loop.run_in_executor(None, self._sync_read_file, log_file, tail)
            return lines
        except Exception as e:
            logger.error(f"读取分布式日志失败: {e}")
            return []

    @staticmethod
    def _sync_read_file(file_path, tail):
        """同步读取文件（在线程池中执行）"""
        with open(file_path, encoding="utf-8") as f:
            if tail is None:
                return [line.rstrip() for line in f]
            window = deque(maxlen=tail)
            for line in f:
                window.append(line.rstrip())
            return list(window)

    async def get_task_status(self, run_id):
        """获取任务状态"""
        if run_id in self._task_status:
            return self._task_status[run_id]

        log_dir = self._find_log_dir(run_id)
        if not log_dir:
            return None

        status_file = os.path.join(log_dir, "status.json")
        if os.path.exists(status_file):
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: json_load_file(status_file))
            except Exception:
                pass
        return None

    async def get_all_logs(self, run_id):
        """获取所有类型的日志"""
        return {
            "stdout": await self.get_logs(run_id, "stdout", 5000),
            "stderr": await self.get_logs(run_id, "stderr", 5000),
        }

    def clear_cache(self, run_id):
        """清理指定执行ID的缓存"""
        for log_type in ["stdout", "stderr"]:
            cache_key = f"{run_id}:{log_type}"
            if cache_key in self._log_cache:
                del self._log_cache[cache_key]
            if cache_key in self._write_buffers:
                del self._write_buffers[cache_key]
            flush_task = self._flush_tasks.pop(cache_key, None)
            if flush_task and not flush_task.done():
                flush_task.cancel()

        if run_id in self._task_status:
            del self._task_status[run_id]
        if run_id in self._file_locks:
            del self._file_locks[run_id]
        if run_id in self._ws_connection_cache:
            del self._ws_connection_cache[run_id]
        if run_id in self._ws_cache_time:
            del self._ws_cache_time[run_id]
        ws_task = self._ws_tasks.pop(run_id, None)
        if ws_task and not ws_task.done():
            ws_task.cancel()
        if run_id in self._ws_queues:
            del self._ws_queues[run_id]

    async def cleanup_old_logs(self, days=7):
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
