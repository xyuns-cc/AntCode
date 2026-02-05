"""工作引擎 - 任务调度与执行的核心"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from loguru import logger

from .scheduler import Scheduler, ProjectType, TaskPriority
from .signals import Signal, signal_manager


class EngineState(Enum):
    IDLE = auto()
    STARTING = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()


@dataclass
class EngineConfig:
    max_queue_size: int = 10000
    max_concurrent_tasks: int = 5
    default_timeout: int = 3600
    task_timeout: int = 3600
    persist_path: str = None
    logs_dir: str = None


class WorkerEngine:
    def __init__(self, config=None, signals=None):
        self.config = config or EngineConfig()
        self.signals = signals or signal_manager
        self._state = EngineState.IDLE
        self._start_time = None
        self.scheduler = Scheduler(signals=self.signals, max_queue_size=self.config.max_queue_size,
                                   persist_path=self.config.persist_path)
        self._project_service = None
        self._env_service = None
        self._master_client = None
        self._on_task_start = None
        self._on_task_complete = None
        self._on_log_line = None
        self._process_task = None
        self._running = False
        self._running_tasks = {}
        self._heartbeat_tasks = {}  # 任务心跳协程
        self._stats = {"tasks_received": 0, "tasks_completed": 0, "tasks_failed": 0}

    @property
    def state(self):
        return self._state

    @property
    def running_count(self):
        return len(self._running_tasks)

    @property
    def pending_count(self):
        return self.scheduler.size

    @property
    def max_concurrent(self):
        return self.config.max_concurrent_tasks


    def set_services(self, project_service=None, env_service=None, master_client=None):
        self._project_service = project_service
        self._env_service = env_service
        self._master_client = master_client

    def set_callbacks(self, on_task_start=None, on_task_complete=None, on_log_line=None):
        self._on_task_start = on_task_start
        self._on_task_complete = on_task_complete
        self._on_log_line = on_log_line

    def on_signal(self, signal, callback):
        self.signals.connect(signal, callback)

    async def start(self):
        if self._state not in (EngineState.IDLE, EngineState.STOPPED):
            return
        self._state = EngineState.STARTING
        self._start_time = datetime.now()
        await self.scheduler.start()
        self._running = True
        self._state = EngineState.RUNNING
        self._process_task = asyncio.create_task(self._process_loop())
        await self.signals.send_catch_log(Signal.ENGINE_STARTED, sender=self)
        logger.info("引擎已启动")

    async def stop(self, graceful: bool = True, timeout: int = 30):
        """停止引擎
        
        Args:
            graceful: 是否优雅关闭 (等待当前任务完成)
            timeout: 优雅关闭超时时间 (秒)
        """
        if self._state == EngineState.STOPPED:
            return
        self._state = EngineState.STOPPING
        self._running = False

        # 停止接收新任务
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass

        # 优雅关闭: 等待当前任务完成
        if graceful and self._running_tasks:
            logger.info(f"等待 {len(self._running_tasks)} 个任务完成 (超时: {timeout}s)")
            try:
                await asyncio.wait_for(
                    self._wait_running_tasks(),
                    timeout=timeout
                )
                logger.info("所有任务已完成")
            except asyncio.TimeoutError:
                logger.warning(f"优雅关闭超时，强制取消 {len(self._running_tasks)} 个任务")
                for task in self._running_tasks.values():
                    task.cancel()
        else:
            # 强制取消所有任务
            for task in self._running_tasks.values():
                task.cancel()

        # 取消所有心跳任务
        for heartbeat_task in self._heartbeat_tasks.values():
            heartbeat_task.cancel()
        self._heartbeat_tasks.clear()

        await self.scheduler.stop()
        self._state = EngineState.STOPPED
        await self.signals.send_catch_log(Signal.ENGINE_STOPPED, sender=self)
        logger.info("引擎已停止")

    async def _wait_running_tasks(self):
        """等待所有运行中的任务完成"""
        while self._running_tasks:
            await asyncio.sleep(0.5)

    async def create_task(self, project_id, params=None, environment_vars=None, timeout=None,
                         priority=TaskPriority.NORMAL.value, project_type=ProjectType.CODE):
        import uuid
        task_id = str(uuid.uuid4())
        self._stats["tasks_received"] += 1
        await self.scheduler.enqueue(task_id=task_id, project_id=project_id, project_type=project_type,
                                     priority=priority, data={"params": params or {}, "environment": environment_vars or {},
                                                             "timeout": timeout or self.config.default_timeout})
        return {"task_id": task_id, "project_id": project_id, "status": "queued"}

    async def cancel_task(self, task_id):
        if task_id in self._running_tasks:
            self._running_tasks[task_id].cancel()
            return True
        return await self.scheduler.cancel(task_id)

    async def list_tasks(self, status=None, limit=50):
        return self.scheduler.get_details()[:limit]

    async def get_task(self, task_id):
        if self.scheduler.contains(task_id):
            for task in self.scheduler.get_details():
                if task["task_id"] == task_id:
                    return task
        return None

    def get_stats(self):
        uptime = int((datetime.now() - self._start_time).total_seconds()) if self._start_time else 0
        return {"state": self._state.name, "uptime": uptime, **self._stats, "scheduler": self.scheduler.get_stats()}

    async def update_config(self, updates: dict):
        """动态更新引擎配置"""
        if "max_concurrent_tasks" in updates:
            self.config.max_concurrent_tasks = updates["max_concurrent_tasks"]
            logger.info(f"引擎并发数已更新: {updates['max_concurrent_tasks']}")
        if "task_timeout" in updates:
            self.config.task_timeout = updates["task_timeout"]
            self.config.default_timeout = updates["task_timeout"]
        # task_memory_limit_mb 和 task_cpu_time_limit_sec 在每次任务执行时从 node_config 读取
        logger.info(f"引擎配置已更新: {updates}")


    async def _process_loop(self):
        while self._running:
            try:
                if self._state == EngineState.PAUSED:
                    await asyncio.sleep(1.0)
                    continue
                if len(self._running_tasks) >= self.config.max_concurrent_tasks:
                    await asyncio.sleep(0.1)
                    continue
                task = await self.scheduler.dequeue(timeout=1.0)
                if task:
                    asyncio.create_task(self._execute_task(task))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"处理循环异常: {e}")
                await asyncio.sleep(1.0)

    async def _resolve_project(self, task_id: str, project_id: str, task_data: dict) -> tuple:
        """解析项目信息
        
        Returns:
            (project_path, entry_point, project_info)
        """
        if not self._project_service:
            return None, "main.py", None

        # 首先尝试通过 master_project_id 查找本地缓存的项目
        local_project_id = self._project_service.get_cached_project_id(project_id)

        if local_project_id:
            project_info = await self._project_service.get_project(local_project_id)
        else:
            project_info = await self._project_service.get_project(project_id)

        # 如果项目未找到，尝试使用任务数据中的信息重新同步
        if not project_info and task_data.get("download_url") and task_data.get("access_token"):
            logger.info(f"任务 {task_id} 项目未找到，尝试重新同步: {project_id}")
            try:
                project_info = await self._project_service.sync_from_master(
                    master_project_id=project_id,
                    project_name=f"project-{project_id[:8]}",
                    download_url=task_data["download_url"],
                    access_token=task_data["access_token"],
                    entry_point=task_data.get("entry_point"),
                    file_hash=task_data.get("file_hash"),
                )
                if project_info:
                    logger.info(f"任务 {task_id} 项目重新同步成功")
            except Exception as e:
                logger.error(f"任务 {task_id} 项目重新同步失败: {e}")

        if not project_info:
            logger.warning(f"任务 {task_id} 未找到项目 {project_id}")
            return None, "main.py", None

        # 获取项目工作目录和入口文件
        actual_project_id = project_info.get("id", local_project_id or project_id)
        project_path = self._project_service.get_project_work_dir(actual_project_id)

        # 优先使用 entry_point，其次使用 original_name（单文件项目），最后使用 main.py
        entry_point = project_info.get("entry_point")
        if not entry_point:
            original_name = project_info.get("original_name", "")
            entry_point = original_name if original_name.endswith(".py") else "main.py"

        logger.info(f"任务 {task_id} 项目路径: {project_path}, 入口: {entry_point}")
        return project_path, entry_point, project_info

    async def _resolve_python_executable(self, project_info: dict) -> str:
        """解析 Python 解释器路径"""
        if not self._env_service or not project_info:
            return "python"

        env_name = project_info.get("env_name")
        if env_name:
            env_info = await self._env_service.get_env(env_name)
            if env_info:
                return env_info.get("python_bin", "python")
        return "python"

    async def _heartbeat_loop(self, execution_id: str):
        """任务执行期间的心跳上报"""
        from ..services import master_client

        interval = 30  # 心跳间隔 30 秒
        while execution_id in self._running_tasks:
            try:
                await asyncio.sleep(interval)
                if execution_id not in self._running_tasks:
                    break
                if master_client.is_connected:
                    await master_client.report_execution_heartbeat(execution_id)
                    logger.debug(f"任务心跳已上报: {execution_id}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"心跳上报失败: {e}")

    async def _run_executor(self, task_id: str, project_id: str, project_path: str,
                            entry_point: str, python_executable: str, task_data: dict) -> dict:
        """运行执行器"""
        from ..executors import ExecutorFactory, ExecutionContext
        from ..config import get_node_config

        node_config = get_node_config()
        cpu_limit = node_config.task_cpu_time_limit_sec if node_config.task_cpu_time_limit_sec > 0 else None
        memory_limit = node_config.task_memory_limit_mb if node_config.task_memory_limit_mb > 0 else None

        # 根据任务数据选择执行器类型
        executor_type = task_data.get("executor_type", "code")
        enable_security = task_data.get("enable_security_scan", False)

        executor = ExecutorFactory.create(
            executor_type=executor_type,
            signals=self.signals,
            max_concurrent=1,
            cpu_limit=cpu_limit,
            memory_limit=memory_limit,
            enable_security_scan=enable_security
        )
        if self._on_log_line:
            executor.set_log_callback(self._on_log_line)

        # 启动心跳上报
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(task_id))
        self._heartbeat_tasks[task_id] = heartbeat_task

        await executor.start()
        try:
            context = ExecutionContext(
                execution_id=task_id,
                project_id=project_id,
                project_path=project_path,
                entry_point=entry_point,
                python_executable=python_executable,
                work_dir=project_path,
                params=task_data.get("params", {}),
                environment=task_data.get("environment", {}),
                timeout=task_data.get("timeout", self.config.default_timeout),
                cpu_limit=cpu_limit,
                memory_limit=memory_limit
            )
            exec_result = await executor.execute(context)
            return {
                "task_id": task_id,
                "execution_id": task_id,
                "project_id": project_id,
                "status": exec_result.status.value,
                "exit_code": exec_result.exit_code,
                "error_message": exec_result.error_message,
                "duration_ms": exec_result.duration_ms
            }
        finally:
            # 停止心跳上报
            heartbeat_task.cancel()
            self._heartbeat_tasks.pop(task_id, None)
            await executor.stop()

    async def _execute_task(self, task):
        """执行任务主流程"""
        task_id = task.task_id
        project_id = task.project_id
        task_data = task.data or {}

        try:
            self._running_tasks[task_id] = asyncio.current_task()
            await self.signals.send_catch_log(Signal.TASK_STARTED, sender=self, task_id=task_id, project_id=project_id)

            if self._on_task_start:
                await self._on_task_start({
                    "task_id": task_id,
                    "execution_id": task_id,
                    "project_id": project_id,
                })

            # 1. 解析项目
            project_path, entry_point, project_info = await self._resolve_project(task_id, project_id, task_data)

            # 2. 解析 Python 解释器
            python_executable = await self._resolve_python_executable(project_info) if project_path else "python"

            # 3. 执行任务
            if project_path:
                result = await self._run_executor(task_id, project_id, project_path,
                                                  entry_point, python_executable, task_data)
            else:
                logger.error(f"任务 {task_id} 项目不可用，无法执行")
                result = {
                    "task_id": task_id,
                    "execution_id": task_id,
                    "project_id": project_id,
                    "status": "failed",
                    "exit_code": 1,
                    "error_message": "项目不可用，无法执行",
                }

            # 4. 更新统计
            if result["status"] == "success":
                self._stats["tasks_completed"] += 1
            else:
                self._stats["tasks_failed"] += 1

            await self.signals.send_catch_log(Signal.TASK_COMPLETED, sender=self, task_id=task_id, result=result)
            if self._on_task_complete:
                await self._on_task_complete(result)

        except asyncio.CancelledError:
            self._stats["tasks_failed"] += 1
            await self.signals.send_catch_log(Signal.TASK_CANCELLED, sender=self, task_id=task_id)
            raise
        except Exception as e:
            self._stats["tasks_failed"] += 1
            logger.error(f"任务执行失败 [{task_id}]: {e}")
            await self.signals.send_catch_log(Signal.TASK_FAILED, sender=self, task_id=task_id, error=str(e))
            if self._on_task_complete:
                await self._on_task_complete({
                    "task_id": task_id, "execution_id": task_id, 
                    "status": "failed", "error_message": str(e)
                })
        finally:
            self._running_tasks.pop(task_id, None)


_worker_engine = None

def get_worker_engine():
    return _worker_engine

def init_worker_engine(config=None, **kwargs):
    global _worker_engine
    if config is None:
        config = EngineConfig(max_queue_size=kwargs.get("max_queue_size", 10000),
                             max_concurrent_tasks=kwargs.get("max_concurrent_tasks", 5),
                             task_timeout=kwargs.get("default_timeout", kwargs.get("task_timeout", 3600)))
    _worker_engine = WorkerEngine(config=config)
    return _worker_engine
