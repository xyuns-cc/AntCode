"""代码执行器"""
import asyncio
import contextlib
import os
from datetime import datetime
from loguru import logger

try:
    import psutil
except ImportError:
    psutil = None

from .base import BaseExecutor, ExecutionResult, ExecutionStatus


class CodeExecutor(BaseExecutor):
    def __init__(self, signals=None, max_concurrent=5, default_timeout=3600, cpu_limit=None, memory_limit=None):
        super().__init__(signals, max_concurrent, default_timeout)
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit

    async def _do_execute(self, context):
        result = ExecutionResult(execution_id=context.execution_id, status=ExecutionStatus.RUNNING,
                                started_at=datetime.now().isoformat())
        try:
            cmd = [context.python_executable, context.entry_point]
            cmd.extend(self._build_args(context.params))
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{context.work_dir}:{env.get('PYTHONPATH', '')}"
            env.update(context.environment)

            process = await asyncio.create_subprocess_exec(*cmd, cwd=context.work_dir, env=env,
                                                          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async with self._lock:
                self._running_tasks[context.execution_id] = process

            monitor_task = None
            cpu_lim = context.cpu_limit or self.cpu_limit
            mem_lim = context.memory_limit or self.memory_limit
            if psutil and (cpu_lim or mem_lim):
                monitor_task = asyncio.create_task(self._monitor_resources(process, context.execution_id, cpu_lim, mem_lim))

            try:
                exit_code = await self._stream_output(process, context.execution_id, result, context.timeout or self.default_timeout)
                result.exit_code = exit_code
                result.finished_at = datetime.now().isoformat()
                if exit_code == 0:
                    result.status = ExecutionStatus.SUCCESS
                elif exit_code == 124:
                    result.status = ExecutionStatus.TIMEOUT
                    result.error_message = f"执行超时 ({context.timeout}s)"
                elif exit_code in (-15, -9):
                    result.status = ExecutionStatus.CANCELLED
                    result.error_message = "任务被取消"
                else:
                    result.status = ExecutionStatus.FAILED
                    result.error_message = f"退出码: {exit_code}"
            finally:
                if monitor_task:
                    monitor_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await monitor_task
                async with self._lock:
                    self._running_tasks.pop(context.execution_id, None)
        except Exception as e:
            logger.error(f"执行异常: {e}")
            result.status = ExecutionStatus.FAILED
            result.error_message = str(e)
            result.finished_at = datetime.now().isoformat()
        return result


    async def _stream_output(self, process, execution_id, result, timeout):
        """流式读取输出 (委托给基类)"""
        return await self._stream_output_base(process, execution_id, result, timeout)

    async def _monitor_resources(self, process, execution_id, cpu_limit, memory_limit):
        if not psutil:
            return
        try:
            p = psutil.Process(process.pid)
        except Exception:
            return
        memory_limit_bytes = (memory_limit or 0) * 1024 * 1024
        try:
            while process.returncode is None:
                with contextlib.suppress(psutil.NoSuchProcess):
                    cpu_time = sum(p.cpu_times()[:2])
                    rss = p.memory_info().rss
                    if cpu_limit and cpu_time > cpu_limit:
                        logger.warning(f"CPU 时间超限: {cpu_time:.1f}s > {cpu_limit}s")
                        p.kill()
                        break
                    if memory_limit_bytes and rss > memory_limit_bytes:
                        logger.warning(f"内存超限: {rss/1024/1024:.1f}MB > {memory_limit}MB")
                        p.kill()
                        break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"资源监控异常: {e}")

    async def cancel(self, execution_id):
        """取消任务执行 (委托给基类)"""
        return await self._cancel_process(execution_id)
