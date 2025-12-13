"""执行器基类"""
import asyncio
import importlib.util
import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Type
from loguru import logger

from ..core.signals import Signal


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class ExecutionContext:
    execution_id: str
    project_id: str
    project_path: str
    entry_point: str
    python_executable: str
    work_dir: str
    params: dict = field(default_factory=dict)
    environment: dict = field(default_factory=dict)
    timeout: int = 3600
    cpu_limit: int = None
    memory_limit: int = None


@dataclass
class ExecutionResult:
    execution_id: str
    status: ExecutionStatus
    exit_code: int = None
    error_message: str = None
    started_at: str = None
    finished_at: str = None
    duration_ms: float = 0
    stdout_lines: int = 0
    stderr_lines: int = 0
    data: dict = field(default_factory=dict)

    def to_dict(self):
        return {"execution_id": self.execution_id, "status": self.status.value, "exit_code": self.exit_code,
                "error_message": self.error_message, "started_at": self.started_at, "finished_at": self.finished_at,
                "duration_ms": self.duration_ms, "stdout_lines": self.stdout_lines, "stderr_lines": self.stderr_lines, "data": self.data}


class BaseExecutor(ABC):
    def __init__(self, signals=None, max_concurrent=5, default_timeout=3600):
        self.signals = signals
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self._running_tasks = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._running = False
        self._on_log = None
        self._stats = {"total_executions": 0, "completed": 0, "failed": 0, "cancelled": 0, "timeout": 0}

    def set_log_callback(self, callback):
        self._on_log = callback

    def _build_args(self, params: dict) -> list:
        """构建命令行参数 (公共方法)"""
        args = []
        if not params:
            return args
        if "_args" in params:
            pos_args = params["_args"]
            if isinstance(pos_args, list):
                args.extend(str(a) for a in pos_args)
            else:
                args.append(str(pos_args))
        for key, value in params.items():
            if key == "_args":
                continue
            if isinstance(value, bool):
                if value:
                    args.append(f"--{key}")
            elif isinstance(value, list):
                args.append(f"--{key}")
                args.extend(str(v) for v in value)
            elif value is not None:
                args.extend([f"--{key}", str(value)])
        return args

    async def _load_module_class(
        self,
        project_path: str,
        entry_point: str,
        base_class: Optional[Type] = None,
        class_name_hint: Optional[str] = None,
        module_name: str = "dynamic_module",
        check_methods: Optional[list] = None,
    ) -> Optional[Type]:
        """
        通用的模块类加载方法 (公共方法)
        
        从指定文件加载模块并查找符合条件的类。
        
        Args:
            project_path: 项目路径
            entry_point: 入口文件相对路径
            base_class: 基类类型（用于 issubclass 检查）
            class_name_hint: 类名提示（排除此名称的类）
            module_name: 模块名称
            check_methods: 需要检查的方法列表（类必须具有这些方法）
            
        Returns:
            找到的类，或 None
        """
        try:
            module_file = os.path.join(project_path, entry_point)

            if not os.path.exists(module_file):
                logger.error(f"模块文件不存在: {module_file}")
                return None

            spec = importlib.util.spec_from_file_location(module_name, module_file)
            if not spec or not spec.loader:
                logger.error(f"无法创建模块规范: {module_file}")
                return None

            module = importlib.util.module_from_spec(spec)

            # 确保项目路径在 sys.path 中
            if project_path not in sys.path:
                sys.path.insert(0, project_path)

            spec.loader.exec_module(module)

            # 查找符合条件的类
            for name in dir(module):
                obj = getattr(module, name)

                # 必须是类
                if not isinstance(obj, type):
                    continue

                # 排除类名提示（通常是基类本身）
                if class_name_hint and obj.__name__ == class_name_hint:
                    continue

                # 检查是否是基类的子类
                if base_class is not None:
                    if not issubclass(obj, base_class) or obj is base_class:
                        continue
                    return obj

                # 如果没有基类，检查是否具有指定方法
                if check_methods:
                    has_all_methods = all(hasattr(obj, method) for method in check_methods)
                    if has_all_methods:
                        return obj

            logger.error(f"未找到符合条件的类: {module_file}")
            return None

        except Exception as e:
            logger.error(f"加载模块失败: {e}")
            return None

    async def _stream_output_base(self, process, execution_id: str, result: 'ExecutionResult', 
                                   timeout: int, max_lines: int = 100000) -> int:
        """流式读取输出 (公共方法)"""
        async def read_stream(stream, stream_type: str) -> int:
            count = 0
            while True:
                line = await stream.readline()
                if not line:
                    break
                count += 1
                if count > max_lines:
                    logger.warning(f"任务 {execution_id} 输出行数超限 ({max_lines})")
                    continue
                content = line.decode("utf-8", errors="ignore").rstrip()
                if self._on_log:
                    try:
                        callback_result = self._on_log(execution_id, stream_type, content)
                        if asyncio.iscoroutine(callback_result):
                            await callback_result
                    except Exception:
                        pass
            return count

        try:
            stdout_task = asyncio.create_task(read_stream(process.stdout, "stdout"))
            stderr_task = asyncio.create_task(read_stream(process.stderr, "stderr"))
            wait_task = asyncio.create_task(process.wait())
            done, pending = await asyncio.wait([stdout_task, stderr_task, wait_task], 
                                               timeout=timeout, return_when=asyncio.ALL_COMPLETED)
            if pending:
                for task in pending:
                    task.cancel()
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                return 124
            result.stdout_lines = stdout_task.result() if stdout_task.done() else 0
            result.stderr_lines = stderr_task.result() if stderr_task.done() else 0
            return process.returncode or 0
        except asyncio.TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            return 124

    async def _cancel_process(self, execution_id: str) -> bool:
        """取消进程执行 (公共方法)"""
        async with self._lock:
            process = self._running_tasks.get(execution_id)
            if not process:
                return False
        try:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            # 从运行任务列表中移除
            async with self._lock:
                self._running_tasks.pop(execution_id, None)
            logger.info(f"任务已取消: {execution_id}")
            return True
        except Exception as e:
            logger.error(f"取消任务失败: {e}")
            return False

    async def start(self):
        self._running = True
        logger.info(f"{self.__class__.__name__} 已启动 (并发: {self.max_concurrent})")

    async def stop(self):
        self._running = False
        for execution_id in list(self._running_tasks.keys()):
            await self.cancel(execution_id)
        logger.info(f"{self.__class__.__name__} 已停止")

    async def execute(self, context):
        self._stats["total_executions"] += 1
        start_time = time.time()
        if self.signals:
            await self.signals.send_catch_log(Signal.EXECUTION_STARTED, sender=self, execution_id=context.execution_id)
        async with self._semaphore:
            result = await self._do_execute(context)
        result.duration_ms = (time.time() - start_time) * 1000
        self._update_stats(result.status)
        if self.signals:
            signal = {ExecutionStatus.COMPLETED: Signal.EXECUTION_COMPLETED, ExecutionStatus.FAILED: Signal.EXECUTION_FAILED,
                     ExecutionStatus.CANCELLED: Signal.TASK_CANCELLED}.get(result.status, Signal.EXECUTION_COMPLETED)
            await self.signals.send_catch_log(signal, sender=self, execution_id=context.execution_id, result=result.to_dict())
        return result

    @abstractmethod
    async def _do_execute(self, context):
        pass

    @abstractmethod
    async def cancel(self, execution_id):
        pass

    def _update_stats(self, status):
        if status == ExecutionStatus.COMPLETED:
            self._stats["completed"] += 1
        elif status == ExecutionStatus.FAILED:
            self._stats["failed"] += 1
        elif status == ExecutionStatus.CANCELLED:
            self._stats["cancelled"] += 1
        elif status == ExecutionStatus.TIMEOUT:
            self._stats["timeout"] += 1

    @property
    def running_count(self):
        return len(self._running_tasks)

    @property
    def available_slots(self):
        return self.max_concurrent - len(self._running_tasks)

    def get_stats(self):
        return {**self._stats, "running": len(self._running_tasks), "max_concurrent": self.max_concurrent, "available_slots": self.available_slots}
