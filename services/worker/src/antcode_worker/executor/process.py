"""
进程执行器

基于 subprocess 的任务执行器，实现 stdout/stderr 捕获。

Requirements: 7.2
"""

import asyncio
import contextlib
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    psutil = None
    HAS_PSUTIL = False

try:
    import resource

    HAS_RESOURCE = True
except ImportError:
    resource = None  # type: ignore[assignment]
    HAS_RESOURCE = False

from antcode_worker.domain.enums import ExitReason, LogStream, RunStatus
from antcode_worker.domain.models import (
    ExecPlan,
    ExecResult,
    LogEntry,
    RuntimeHandle,
)
from antcode_worker.executor.base import (
    BaseExecutor,
    ExecutorConfig,
    LogSink,
    NoOpLogSink,
)

# C1: 子进程 env 白名单——只透传给 shell / runtime 必需的变量；
# 其它一律不传给用户代码。防止 WORKER_API_KEY / WORKER_GATEWAY_TOKEN /
# WORKER_REDIS_PASSWORD / WORKER_CREDENTIAL_SECRET_KEY 泄漏给任务进程。
#
# 允许的 host 环境变量前缀（前缀匹配，例如 LANG*、LC_*）
_ENV_HOST_ALLOWED_EXACT = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "USERNAME",  # Windows
        "TMPDIR",
        "TEMP",
        "TMP",
        "SHELL",
        "TERM",
        "PWD",
        "HOSTNAME",
        "LANG",
        # Node / Python / Go / Java runtime 相关（子进程可能真的需要）
        "NODE_PATH",
        "npm_config_cache",
        "GOCACHE",
        "GOMODCACHE",
        "MAVEN_OPTS",
        "JAVA_HOME",
        "PYTHONHASHSEED",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        # mise
        "MISE_DATA_DIR",
        "MISE_CACHE_DIR",
    }
)
_ENV_HOST_ALLOWED_PREFIX = (
    "LC_",  # locale
    "ANTCODE_TASK_",  # 显式声明给任务用的
    "ANTCODE_SPIDER_",  # spider reporter 配置
)
# 二重防线：即便 exec_plan.env 或前缀匹配放进来，也拒绝这些高风险模式
_ENV_DENY_PATTERNS = ("SECRET", "PASSWORD", "TOKEN", "API_KEY", "CREDENTIAL", "PRIVATE_KEY")
_TRUSTED_PLUGIN_SECRET_ENV = {
    "rule": frozenset({"ANTCODE_SPIDER_GATEWAY_AUTH_TOKEN"}),
}


def _is_env_allowed_from_host(key: str) -> bool:
    if key in _ENV_HOST_ALLOWED_EXACT:
        return True
    return any(key.startswith(p) for p in _ENV_HOST_ALLOWED_PREFIX)


def _is_env_forbidden(key: str, plugin_name: str | None = None) -> bool:
    if key in _TRUSTED_PLUGIN_SECRET_ENV.get(plugin_name or "", frozenset()):
        return False
    upper = key.upper()
    return any(pat in upper for pat in _ENV_DENY_PATTERNS)


def _build_preexec_fn(
    enforce_rlimit: bool,
    cpu_seconds: int,
    memory_mb: int,
    max_open_files: int,
    max_processes: int,
    file_size_mb: int = 0,
) -> Callable[[], None] | None:
    """构造子进程 preexec_fn：独立进程组 + POSIX rlimit。

    None 表示当前平台/配置下无需 preexec_fn（如 Windows 或 enforce_rlimit=False）。

    T7-P2-4: 新增 ``file_size_mb`` → RLIMIT_FSIZE，防止子进程写单个大文件把
    worker 磁盘打爆。POSIX RLIMIT_FSIZE 只限单文件，配合 artifact_cleanup
    的总量控制形成双层防护。
    """
    if sys.platform == "win32":
        return None

    def _pre() -> None:
        try:
            os.setsid()
        except OSError:
            pass
        if not HAS_RESOURCE or resource is None:
            return
        # 每条 setrlimit 独立保护，避免某一项不支持就放弃所有限制
        if cpu_seconds and cpu_seconds > 0 and hasattr(resource, "RLIMIT_CPU"):
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            except (ValueError, OSError):
                pass
        if memory_mb and memory_mb > 0 and hasattr(resource, "RLIMIT_AS"):
            limit = memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (ValueError, OSError):
                pass
        if max_open_files and max_open_files > 0 and hasattr(resource, "RLIMIT_NOFILE"):
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (max_open_files, max_open_files))
            except (ValueError, OSError):
                pass
        if max_processes and max_processes > 0 and hasattr(resource, "RLIMIT_NPROC"):
            try:
                resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
            except (ValueError, OSError):
                pass
        # T7-P2-4: RLIMIT_FSIZE 单文件上限
        if file_size_mb and file_size_mb > 0 and hasattr(resource, "RLIMIT_FSIZE"):
            limit = file_size_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
            except (ValueError, OSError):
                pass

    return _pre


def _signal_process_group(process: asyncio.subprocess.Process, sig: int) -> None:
    """优先按进程组发送信号（杀掉子进程及其 fork 出来的孙进程）。

    若拿不到进程组（如 setsid 失败、Windows），退回到给主进程发信号。
    """
    if process.returncode is not None:
        return
    pid = process.pid
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, sig)
        return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.send_signal(sig)
    except ProcessLookupError:
        pass


@dataclass
class ProcessInfo:
    """进程信息"""

    process: asyncio.subprocess.Process
    run_id: str
    started_at: datetime
    exec_plan: ExecPlan
    cancelled: bool = False

    # 资源使用
    cpu_time_seconds: float = 0
    memory_peak_mb: float = 0


class ProcessExecutor(BaseExecutor):
    """
    进程执行器

    在独立子进程中执行任务，支持：
    - stdout/stderr 实时捕获
    - 超时控制（SIGTERM -> grace period -> SIGKILL）
    - 资源监控（CPU/内存）
    - 取消支持

    Requirements: 7.2
    """

    def __init__(self, config: ExecutorConfig | None = None):
        """
        初始化进程执行器

        Args:
            config: 执行器配置
        """
        super().__init__(config)

    async def run(
        self,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink | None = None,
    ) -> ExecResult:
        """
        执行任务

        Args:
            exec_plan: 执行计划
            runtime_handle: 运行时句柄
            log_sink: 日志接收器

        Returns:
            ExecResult 执行结果

        Requirements: 7.2
        """
        # 生成 run_id（如果 exec_plan 没有提供）
        run_id = exec_plan.run_id or exec_plan.plugin_name or f"run_{int(time.time() * 1000)}"

        # 使用空日志接收器如果未提供
        sink = log_sink or NoOpLogSink()

        # 获取信号量
        async with self._semaphore:
            return await self._execute(run_id, exec_plan, runtime_handle, sink)

    async def _execute(
        self,
        run_id: str,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink,
    ) -> ExecResult:
        """执行任务的内部实现"""
        started_at = datetime.now()
        process_info: ProcessInfo | None = None

        # 序列号计数器
        seq_counter = {"stdout": 0, "stderr": 0}

        try:
            # 构建命令
            cmd = self._build_command(exec_plan, runtime_handle)

            # 构建环境变量
            env = self._build_env(exec_plan, runtime_handle)

            # 确定工作目录
            cwd = exec_plan.cwd or runtime_handle.path

            logger.debug(f"执行命令: {' '.join(cmd)}, cwd={cwd}")

            # 构造 preexec_fn：独立进程组 + POSIX rlimit
            enforce = exec_plan.enforce_rlimit if exec_plan.enforce_rlimit is not None else self.config.enforce_rlimit
            preexec = _build_preexec_fn(
                enforce_rlimit=bool(enforce),
                cpu_seconds=exec_plan.cpu_limit_seconds or self.config.default_cpu_limit_seconds,
                memory_mb=exec_plan.memory_limit_mb or self.config.default_memory_limit_mb,
                max_open_files=exec_plan.max_open_files or self.config.default_max_open_files,
                max_processes=exec_plan.max_processes or self.config.default_max_processes,
                # T7-P2-4: RLIMIT_FSIZE 单文件上限
                file_size_mb=(exec_plan.max_file_size_mb or getattr(self.config, "default_max_file_size_mb", 0)),
            )

            # 创建子进程
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=preexec,
            )

            # 创建进程信息
            process_info = ProcessInfo(
                process=process,
                run_id=run_id,
                started_at=started_at,
                exec_plan=exec_plan,
            )

            # 注册任务
            await self._register_task(run_id, process_info)

            # 启动资源监控
            monitor_task = None
            if HAS_PSUTIL and (exec_plan.memory_limit_mb > 0 or exec_plan.cpu_limit_seconds > 0):
                monitor_task = asyncio.create_task(self._monitor_resources(process_info))

            try:
                # 流式读取输出
                exit_code, stdout_lines, stderr_lines = await self._stream_output(
                    process_info,
                    log_sink,
                    seq_counter,
                    exec_plan.timeout_seconds or self.config.default_timeout,
                )

                # 刷新日志
                await log_sink.flush()

                # 确定状态和退出原因
                status, exit_reason, error_msg = self._determine_result(exit_code, process_info)

                # 创建结果
                result = self._create_result(
                    run_id=run_id,
                    status=status,
                    exit_code=exit_code,
                    exit_reason=exit_reason,
                    error_message=error_msg,
                    started_at=started_at,
                    finished_at=datetime.now(),
                    stdout_lines=stdout_lines,
                    stderr_lines=stderr_lines,
                    cpu_time_seconds=process_info.cpu_time_seconds,
                    memory_peak_mb=process_info.memory_peak_mb,
                )

                # 更新统计
                self._update_stats(status)

                return result

            except asyncio.CancelledError:
                await self._terminate_process(
                    process_info,
                    exec_plan.grace_period_seconds or self.config.default_grace_period,
                )
                raise

            finally:
                # 停止资源监控
                if monitor_task:
                    monitor_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await monitor_task

                # 注销任务
                await self._unregister_task(run_id)

        except Exception as e:
            logger.error(f"执行异常: {run_id}, error={e}")

            result = self._create_result(
                run_id=run_id,
                status=RunStatus.FAILED,
                exit_reason=ExitReason.ERROR,
                error_message=str(e),
                started_at=started_at,
                finished_at=datetime.now(),
            )

            self._update_stats(RunStatus.FAILED)
            return result

    def _build_command(self, exec_plan: ExecPlan, runtime_handle: RuntimeHandle) -> list[str]:
        """构建执行命令"""
        # 使用运行时的 Python 解释器
        if exec_plan.command.endswith(".py"):
            cmd = [runtime_handle.python_executable, exec_plan.command]
        else:
            cmd = [exec_plan.command]

        # 添加参数
        cmd.extend(exec_plan.args)

        return cmd

    def _build_env(self, exec_plan: ExecPlan, runtime_handle: RuntimeHandle) -> dict[str, str]:
        """构建子进程环境变量（C1 白名单模式）。

        用户代码运行时**不继承**宿主 worker 的 secrets。规则：
        1. 只从 host env 透传 ``_ENV_HOST_ALLOWED_EXACT/PREFIX`` 允许的键。
        2. runtime 相关键（PATH / VIRTUAL_ENV / PYTHONPATH）由本方法显式构造。
        3. 覆盖 ``exec_plan.env``（任务自带 env，比如 spider reporter 配置）。
        4. 二重防线：所有键在最后过一次 ``_ENV_DENY_PATTERNS`` 黑名单。
        """
        host_env = os.environ

        # 1. 白名单透传
        env: dict[str, str] = {key: value for key, value in host_env.items() if _is_env_allowed_from_host(key)}

        # 2. runtime 显式注入
        pythonpath_parts = [runtime_handle.path]
        existing_pythonpath = host_env.get("PYTHONPATH", "")
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

        env["VIRTUAL_ENV"] = runtime_handle.path
        bin_dir = "Scripts" if os.name == "nt" else "bin"
        venv_bin = os.path.join(runtime_handle.path, bin_dir)
        existing_path = env.get("PATH") or host_env.get("PATH", "")
        env["PATH"] = os.pathsep.join([venv_bin, existing_path]) if existing_path else venv_bin

        # 3. 任务显式环境变量
        env.update(exec_plan.env)

        # 4. 二重防线：剔除任何命中黑名单模式的键（含被 exec_plan.env 误传的）
        for key in list(env.keys()):
            if _is_env_forbidden(key, exec_plan.plugin_name):
                env.pop(key, None)

        return env

    async def _stream_output(
        self,
        process_info: ProcessInfo,
        log_sink: LogSink,
        seq_counter: dict[str, int],
        timeout: int,
    ) -> tuple[int, int, int]:
        """
        流式读取输出

        Args:
            process_info: 进程信息
            log_sink: 日志接收器
            seq_counter: 序列号计数器
            timeout: 超时时间（秒）

        Returns:
            (exit_code, stdout_lines, stderr_lines)
        """
        process = process_info.process
        run_id = process_info.run_id
        max_lines = self.config.max_output_lines

        stdout_count = 0
        stderr_count = 0

        async def read_stream(stream: asyncio.StreamReader, stream_type: str) -> int:
            """读取单个流

            V8: 即使超过 max_lines 也必须继续 ``await readline()``,
            否则 pipe buffer 满后子进程下一次 ``write`` 会阻塞,整个任务卡死。
            超限后 drop 行内容,但保留 drain 行为。
            """
            nonlocal stdout_count, stderr_count
            count = 0

            while True:
                try:
                    line = await stream.readline()
                    if not line:
                        break

                    count += 1

                    # V8: 超限后只 drop 不 break, 继续读取以释放 pipe buffer。
                    if count > max_lines:
                        if count == max_lines + 1:
                            logger.warning(
                                f"任务 {run_id} {stream_type} 输出行数超限 ({max_lines}); "
                                "后续行将被丢弃,但继续 drain 防止子进程阻塞"
                            )
                        continue

                    # 解码内容
                    content = line.decode("utf-8", errors="replace").rstrip()

                    # 更新序列号
                    seq_counter[stream_type] += 1
                    seq = seq_counter[stream_type]

                    # 创建日志条目
                    entry = LogEntry(
                        run_id=run_id,
                        stream=LogStream.STDOUT if stream_type == "stdout" else LogStream.STDERR,
                        content=content,
                        seq=seq,
                        timestamp=datetime.now(),
                    )

                    # 写入日志
                    await log_sink.write(entry)

                except asyncio.CancelledError:
                    # 任务被取消，正常退出
                    break
                except Exception as e:
                    logger.error(f"读取 {stream_type} 异常: {e}")
                    raise

            return count

        def safe_get_result(task: asyncio.Task) -> int:
            """安全获取任务结果"""
            if task.done() and not task.cancelled():
                return task.result()
            return 0

        stdout_task: asyncio.Task | None = None
        stderr_task: asyncio.Task | None = None
        wait_task: asyncio.Task | None = None
        stdout_count = 0
        stderr_count = 0
        try:
            # 创建读取任务
            if process.stdout is None or process.stderr is None:
                raise RuntimeError("执行进程未创建 stdout/stderr 管道")
            stdout_task = asyncio.create_task(read_stream(process.stdout, "stdout"))
            stderr_task = asyncio.create_task(read_stream(process.stderr, "stderr"))
            wait_task = asyncio.create_task(process.wait())

            # 等待完成或超时
            done, pending = await asyncio.wait(
                [stdout_task, stderr_task, wait_task],
                timeout=timeout,
                return_when=asyncio.ALL_COMPLETED,
            )

            # 处理超时
            if pending:
                for task in pending:
                    task.cancel()
                    # 等待任务取消完成
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

                # 终止进程
                await self._terminate_process(
                    process_info,
                    process_info.exec_plan.grace_period_seconds or self.config.default_grace_period,
                )

                return 124, safe_get_result(stdout_task), safe_get_result(stderr_task)

            # 获取结果
            stdout_count = safe_get_result(stdout_task)
            stderr_count = safe_get_result(stderr_task)

            return process.returncode or 0, stdout_count, stderr_count

        except asyncio.CancelledError:
            for child_task in (stdout_task, stderr_task, wait_task):
                if child_task is not None and not child_task.done():
                    child_task.cancel()
            await self._terminate_process(
                process_info,
                process_info.exec_plan.grace_period_seconds or self.config.default_grace_period,
            )
            for child_task in (stdout_task, stderr_task, wait_task):
                if child_task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await child_task
            raise
        except TimeoutError:
            # 超时处理
            await self._terminate_process(
                process_info,
                process_info.exec_plan.grace_period_seconds or self.config.default_grace_period,
            )
            return 124, stdout_count, stderr_count

    async def _terminate_process(self, process_info: ProcessInfo, grace_period: float) -> None:
        """
        终止进程

        先发送 SIGTERM，等待 grace_period 后发送 SIGKILL。

        Args:
            process_info: 进程信息
            grace_period: 等待时间（秒）
        """
        process = process_info.process

        if process.returncode is not None:
            return

        try:
            # 发送 SIGTERM 到整个进程组（防止 fork 出的子进程残留）
            _signal_process_group(process, signal.SIGTERM)
            logger.debug(f"发送 SIGTERM: {process_info.run_id}")

            try:
                await asyncio.wait_for(process.wait(), timeout=grace_period)
            except TimeoutError:
                # 发送 SIGKILL 到整个进程组
                _signal_process_group(process, signal.SIGKILL)
                logger.debug(f"发送 SIGKILL: {process_info.run_id}")
                await process.wait()

        except ProcessLookupError:
            # 进程已退出
            pass
        except Exception as e:
            logger.error(f"终止进程失败: {e}")

    async def _monitor_resources(self, process_info: ProcessInfo) -> None:
        """
        监控资源使用

        Args:
            process_info: 进程信息
        """
        if not HAS_PSUTIL:
            return

        process = process_info.process
        exec_plan = process_info.exec_plan

        try:
            p = psutil.Process(process.pid)
        except (psutil.NoSuchProcess, ProcessLookupError):
            return

        memory_limit_bytes = exec_plan.memory_limit_mb * 1024 * 1024
        cpu_limit = exec_plan.cpu_limit_seconds

        interval = self._get_monitor_interval(exec_plan)

        try:
            while process.returncode is None:
                try:
                    # 获取 CPU 时间
                    cpu_times = p.cpu_times()
                    cpu_time = cpu_times.user + cpu_times.system
                    process_info.cpu_time_seconds = cpu_time

                    # 获取内存使用
                    memory_info = p.memory_info()
                    memory_mb = memory_info.rss / 1024 / 1024
                    process_info.memory_peak_mb = max(process_info.memory_peak_mb, memory_mb)

                    # 检查 CPU 限制（rlimit 是兜底，监控提前杀防失控）
                    if cpu_limit > 0 and cpu_time > cpu_limit:
                        logger.warning(f"CPU 时间超限: {cpu_time:.1f}s > {cpu_limit}s, run_id={process_info.run_id}")
                        _signal_process_group(process, signal.SIGKILL)
                        break

                    # 检查内存限制
                    if memory_limit_bytes > 0 and memory_info.rss > memory_limit_bytes:
                        logger.warning(
                            f"内存超限: {memory_mb:.1f}MB > {exec_plan.memory_limit_mb}MB, run_id={process_info.run_id}"
                        )
                        _signal_process_group(process, signal.SIGKILL)
                        break

                except psutil.NoSuchProcess:
                    break

                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"资源监控异常: {e}")

    def _get_monitor_interval(self, exec_plan: ExecPlan) -> float:
        """根据任务时长估算监控间隔"""
        timeout = int(exec_plan.timeout_seconds or 0)
        if timeout <= 0:
            return 1.0
        if timeout <= 60:
            return 0.5
        if timeout <= 300:
            return 1.0
        if timeout <= 1800:
            return 2.0
        return 5.0

    def _determine_result(self, exit_code: int, process_info: ProcessInfo) -> tuple[RunStatus, ExitReason, str | None]:
        """
        确定执行结果

        Args:
            exit_code: 退出码
            process_info: 进程信息

        Returns:
            (status, exit_reason, error_message)
        """
        if process_info.cancelled:
            return RunStatus.CANCELLED, ExitReason.CANCELLED, "任务被取消"

        if exit_code == 0:
            return RunStatus.SUCCESS, ExitReason.NORMAL, None

        if exit_code == 124:
            return (
                RunStatus.TIMEOUT,
                ExitReason.TIMEOUT,
                f"执行超时 ({process_info.exec_plan.timeout_seconds}s)",
            )

        if exit_code in (-signal.SIGTERM, -signal.SIGKILL, -15, -9):
            return RunStatus.KILLED, ExitReason.KILLED, "进程被终止"

        # 检查是否因资源限制被终止
        exec_plan = process_info.exec_plan
        if exec_plan.cpu_limit_seconds > 0 and process_info.cpu_time_seconds > exec_plan.cpu_limit_seconds:
            return RunStatus.FAILED, ExitReason.CPU_LIMIT, "CPU 时间超限"

        if exec_plan.memory_limit_mb > 0 and process_info.memory_peak_mb > exec_plan.memory_limit_mb:
            return RunStatus.FAILED, ExitReason.OOM, "内存超限"

        return RunStatus.FAILED, ExitReason.ERROR, f"退出码: {exit_code}"

    async def _do_cancel(self, run_id: str, task_info: Any) -> None:
        """执行取消操作"""
        process_info: ProcessInfo = task_info
        process_info.cancelled = True

        await self._terminate_process(
            process_info,
            process_info.exec_plan.grace_period_seconds or self.config.default_grace_period,
        )
