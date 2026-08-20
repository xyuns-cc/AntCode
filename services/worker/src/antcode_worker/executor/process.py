"""
进程执行器

基于 subprocess 的任务执行器，实现 stdout/stderr 捕获。

Requirements: 7.2
"""

import asyncio
import contextlib
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from subprocess import SubprocessError
from typing import Any

import psutil
from loguru import logger

from antcode_worker.domain.enums import ExitReason, RunStatus
from antcode_worker.domain.models import (
    ExecPlan,
    ExecResult,
    RuntimeHandle,
)
from antcode_worker.executor.base import (
    BaseExecutor,
    ExecutorConfig,
    LogSink,
    NoOpLogSink,
)
from antcode_worker.executor.concurrency import ExecutionAdmission
from antcode_worker.executor.exec_path import authoritative_path
from antcode_worker.executor.output_stream import OutputByteBudget, OutputReadOptions, read_output_stream
from antcode_worker.executor.process_limits import SandboxLimits, build_preexec_fn, host_max_processes
from antcode_worker.executor.process_signals import signal_process_group
from antcode_worker.executor.python_path import authoritative_python_path
from antcode_worker.executor.resource_sampler import ProcessTreeUsage, sample_process_tree
from antcode_worker.executor.rule_policy import filter_spider_control_env, is_rule_env_allowed

# C1: 子进程 env 白名单——只透传 shell/runtime 必需变量，其余一律不给用户代码，
# 防止 WORKER_API_KEY / GATEWAY_TOKEN / REDIS_PASSWORD / CREDENTIAL_SECRET_KEY 泄漏。
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
        # 只读根下这些目录须重定向到可写卷，少传一个就退回 $HOME 只读默认路径
        "MISE_DATA_DIR",
        "MISE_CACHE_DIR",
        "MISE_STATE_DIR",
    }
)
_ENV_HOST_ALLOWED_PREFIX = (
    "LC_",  # locale
    "ANTCODE_TASK_",  # 显式声明给任务用的
    "ANTCODE_SPIDER_",  # spider reporter 配置
)
# 二重防线：即便 exec_plan.env 或前缀匹配放进来，也拒绝这些高风险模式
_ENV_DENY_PATTERNS = ("SECRET", "PASSWORD", "TOKEN", "API_KEY", "CREDENTIAL", "PRIVATE_KEY")
_PROCESS_HIJACK_ENV = frozenset({"BASH_ENV", "ENV", "PYTHONHOME"})
_PROCESS_HIJACK_PREFIXES = ("LD_", "DYLD_")
# P1-round6 5.3: PIPE 默认 64 KiB 与合同允许的 1 MiB 单行冲突; 提到 1 MiB 对齐合同, 超限行由 read_stream 的 drop 兜底。
_STREAM_READER_LIMIT_BYTES = 1024 * 1024


def _is_env_allowed_from_host(key: str) -> bool:
    if key in _ENV_HOST_ALLOWED_EXACT:
        return True
    return any(key.startswith(p) for p in _ENV_HOST_ALLOWED_PREFIX)


def _is_env_forbidden(
    key: str,
    plugin_name: str | None = None,
    task_env_keys: frozenset[str] = frozenset(),
) -> bool:
    if key in _PROCESS_HIJACK_ENV or key.startswith(_PROCESS_HIJACK_PREFIXES):
        return True
    if plugin_name != "rule" and key in task_env_keys:
        return False
    upper = key.upper()
    return any(pat in upper for pat in _ENV_DENY_PATTERNS)


def _filter_rule_application_env(env: dict[str, str]) -> dict[str, str]:
    """Rule 只获得运行时基础变量与本地 spool 协议变量。"""
    runtime_keys = {*_ENV_HOST_ALLOWED_EXACT, "PYTHONPATH", "VIRTUAL_ENV"}
    return {
        key: value
        for key, value in env.items()
        if key in runtime_keys or key.startswith("LC_") or is_rule_env_allowed(key)
    }


@dataclass(frozen=True)
class _LaunchSpec:
    """一次子进程启动所需的不可变参数。"""

    cmd: list[str]
    env: dict[str, str]
    cwd: str
    exec_plan: ExecPlan


async def _stop_resource_monitor(monitor_task: asyncio.Task[None] | None) -> BaseException | None:
    """停止监控任务；返回它真正的异常（正常结束/被取消返回 None）。"""
    if monitor_task is None:
        return None
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        return None
    except Exception as exc:
        return exc
    return None


def _describe_limit_breach(usage: ProcessTreeUsage, exec_plan: ExecPlan) -> str | None:
    """返回进程树超限描述；未超限返回 None。"""
    cpu_limit = exec_plan.cpu_limit_seconds
    if cpu_limit > 0 and usage.cpu_time_seconds > cpu_limit:
        return f"进程树 CPU 时间超限: {usage.cpu_time_seconds:.1f}s > {cpu_limit}s"
    memory_limit_mb = exec_plan.memory_limit_mb
    if memory_limit_mb > 0 and usage.memory_rss_mb > memory_limit_mb:
        return f"进程树内存超限: {usage.memory_rss_mb:.1f}MB > {memory_limit_mb}MB"
    return None


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


def _limit_breach_result(process_info: ProcessInfo) -> tuple[RunStatus, ExitReason, str] | None:
    """已采样的峰值是否越过 CPU/内存上限；未越过返回 None。"""
    exec_plan = process_info.exec_plan
    if exec_plan.cpu_limit_seconds > 0 and process_info.cpu_time_seconds > exec_plan.cpu_limit_seconds:
        return RunStatus.FAILED, ExitReason.CPU_LIMIT, "CPU 时间超限"
    if exec_plan.memory_limit_mb > 0 and process_info.memory_peak_mb > exec_plan.memory_limit_mb:
        return RunStatus.FAILED, ExitReason.OOM, "内存超限"
    return None


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
        self._launch_cancellations: dict[str, asyncio.Event] = {}
        self._launch_lock = asyncio.Lock()

    async def run(
        self,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink | None = None,
        *,
        startup_event: asyncio.Event | None = None,
        admission: ExecutionAdmission | None = None,
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
        cancel_event = await self._begin_launch(run_id)
        if startup_event is not None:
            startup_event.set()
        if admission is not None:
            await admission.executor_ready()

        # 使用空日志接收器如果未提供
        sink = log_sink or NoOpLogSink()

        try:
            async with self._concurrency_gate.slot():
                if cancel_event.is_set():
                    return self._cancelled_before_start(run_id)
                return await self._execute(run_id, exec_plan, runtime_handle, sink, cancel_event)
        finally:
            await self._finish_launch(run_id, cancel_event)

    async def _execute(
        self,
        run_id: str,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink,
        cancel_event: asyncio.Event,
    ) -> ExecResult:
        """执行任务的内部实现"""
        started_at = datetime.now()

        try:
            cmd = self._build_command(exec_plan, runtime_handle)
            env = self._build_env(exec_plan, runtime_handle)
            cwd = exec_plan.cwd or runtime_handle.path

            # P0-04 (round6): 完整 argv 可能带 token/API_KEY/credentials(Rule 插件
            # 的 target_url、Go/Python CLI 的 --api-key= 等)。用 sanitize_log_message
            # 脱敏后再 log,避免通过 Worker debug 日志外带凭据。
            from antcode_core.common.logging import sanitize_log_message

            logger.debug(f"执行命令: {sanitize_log_message(' '.join(cmd))}, cwd={cwd}")

            process = await self._spawn_process(_LaunchSpec(cmd=cmd, env=env, cwd=cwd, exec_plan=exec_plan))
            process_info = ProcessInfo(
                process=process,
                run_id=run_id,
                started_at=started_at,
                exec_plan=exec_plan,
            )

            await self._register_task(run_id, process_info)
            try:
                if cancel_event.is_set():
                    process_info.cancelled = True
                    await self._terminate_process(process_info, self._grace_period(exec_plan))
                return await self._run_monitored(process_info, log_sink, started_at)
            finally:
                # 注册/注销保持对称：即便上面的终止流程抛错也不会漏掉注销。
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

    async def _spawn_process(self, spec: _LaunchSpec) -> asyncio.subprocess.Process:
        """拉起子进程；沙箱未生效时拒绝启动。

        P1-round6 5.3: asyncio.subprocess StreamReader 默认 buffer 64 KiB,
        业务合同允许 1 MiB 单行 -> readline() 抛 LimitOverrunError 阻塞。
        把 limit 提到 1 MiB 与合同对齐, 超限行仍走 read_stream 的 drop
        兜底(不 break, 保证 drain), 不会因缓冲区大而放大内存。
        """
        sandbox = self._resolve_sandbox_limits(spec.exec_plan, spec.cmd)
        preexec = build_preexec_fn(sandbox)
        try:
            return await asyncio.create_subprocess_exec(
                *spec.cmd,
                cwd=spec.cwd,
                env=spec.env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=preexec,
                limit=_STREAM_READER_LIMIT_BYTES,
            )
        except SubprocessError as exc:
            # B7/B8: CPython 会丢弃 preexec_fn 内部的异常，父进程只拿到一句
            # "Exception occurred in preexec_fn."。补上上下文，让运维知道是哪一组
            # 沙箱设置没能生效（例如 macOS 的 RLIMIT_AS 恒不可设）。
            raise RuntimeError(
                f"子进程沙箱初始化失败（os.setsid 或 setrlimit 未生效），"
                f"拒绝在无沙箱状态下执行；请求的限制: {sandbox.describe()}；原始错误: {exc}"
            ) from exc

    def _resolve_sandbox_limits(self, exec_plan: ExecPlan, cmd: list[str]) -> SandboxLimits:
        """合并 exec_plan 与 ExecutorConfig 默认值，得到本次的沙箱参数。"""
        enforce = exec_plan.enforce_rlimit if exec_plan.enforce_rlimit is not None else self.config.enforce_rlimit
        return SandboxLimits(
            enforce_rlimit=bool(enforce),
            cpu_seconds=exec_plan.cpu_limit_seconds or self.config.default_cpu_limit_seconds,
            memory_mb=exec_plan.memory_limit_mb or self.config.default_memory_limit_mb,
            max_open_files=exec_plan.max_open_files or self.config.default_max_open_files,
            max_processes=host_max_processes(cmd, exec_plan.max_processes or self.config.default_max_processes),
            # T7-P2-4: RLIMIT_FSIZE 单文件上限
            file_size_mb=exec_plan.max_file_size_mb or self.config.default_max_file_size_mb,
        )

    async def _run_monitored(
        self,
        process_info: ProcessInfo,
        log_sink: LogSink,
        started_at: datetime,
    ) -> ExecResult:
        """在资源监控下读取输出并产出结果。"""
        monitor_task = self._start_resource_monitor(process_info)
        try:
            return await self._collect_result(process_info, log_sink, started_at)
        except asyncio.CancelledError:
            await self._terminate_process(process_info, self._grace_period(process_info.exec_plan))
            raise
        finally:
            monitor_error = await _stop_resource_monitor(monitor_task)
            if monitor_error is not None:
                # B8: 监控挂了 = CPU/内存上限失去主动兜底，绝不能把这次执行报成 SUCCESS。
                raise RuntimeError(
                    f"资源监控失败，无法保证 CPU/内存上限: run_id={process_info.run_id}"
                ) from monitor_error

    async def _collect_result(
        self,
        process_info: ProcessInfo,
        log_sink: LogSink,
        started_at: datetime,
    ) -> ExecResult:
        """流式读取输出并组装 ExecResult。"""
        exec_plan = process_info.exec_plan
        exit_code, stdout_lines, stderr_lines = await self._stream_output(
            process_info,
            log_sink,
            {"stdout": 0, "stderr": 0},
            exec_plan.timeout_seconds or self.config.default_timeout,
        )
        await log_sink.flush()

        status, exit_reason, error_msg = self._determine_result(exit_code, process_info)
        result = self._create_result(
            run_id=process_info.run_id,
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
        self._update_stats(status)
        return result

    def _start_resource_monitor(self, process_info: ProcessInfo) -> asyncio.Task[None] | None:
        """仅在确实配置了 CPU/内存上限时才开监控任务。"""
        exec_plan = process_info.exec_plan
        if exec_plan.memory_limit_mb <= 0 and exec_plan.cpu_limit_seconds <= 0:
            return None
        return asyncio.create_task(self._monitor_resources(process_info))

    def _grace_period(self, exec_plan: ExecPlan) -> float:
        return exec_plan.grace_period_seconds or self.config.default_grace_period

    async def cancel(self, run_id: str) -> bool:
        """记录启动期取消，并终止已经注册的进程。"""
        launch_found = await self._request_launch_cancel(run_id)
        process_cancelled = await super().cancel(run_id)
        return launch_found or process_cancelled

    async def stop(self, grace_period: float = 10.0) -> None:
        """停止时同时阻止尚未创建子进程的 launch。"""
        async with self._launch_lock:
            for cancel_event in self._launch_cancellations.values():
                cancel_event.set()
        await super().stop(grace_period)

    async def _begin_launch(self, run_id: str) -> asyncio.Event:
        cancel_event = asyncio.Event()
        async with self._launch_lock:
            self._launch_cancellations[run_id] = cancel_event
        return cancel_event

    async def _finish_launch(self, run_id: str, cancel_event: asyncio.Event) -> None:
        async with self._launch_lock:
            if self._launch_cancellations.get(run_id) is cancel_event:
                self._launch_cancellations.pop(run_id, None)

    async def _request_launch_cancel(self, run_id: str) -> bool:
        async with self._launch_lock:
            cancel_event = self._launch_cancellations.get(run_id)
            if cancel_event is None:
                return False
            cancel_event.set()
            return True

    def _cancelled_before_start(self, run_id: str) -> ExecResult:
        now = datetime.now()
        return self._create_result(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            exit_reason=ExitReason.CANCELLED,
            error_message="任务在进程启动前被取消",
            started_at=now,
            finished_at=now,
        )

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
        2. 合并项目显式配置的 ``exec_plan.env``。
        3. 拒绝动态加载器与 shell 启动文件劫持变量。
        4. 最后由 Worker 权威写入 PATH / VIRTUAL_ENV / PYTHONPATH——这里是子进程
           启动前的最后一个写者，任何上游算出的同名值都以这里为准。PATH 与
           PYTHONPATH 的拼装分别交给 ``exec_path`` / ``python_path``，沙箱层共用
           同一实现，避免两层各算一份后长值被短值静默盖掉。
        """
        host_env = os.environ

        # 1. 白名单透传
        env: dict[str, str] = {key: value for key, value in host_env.items() if _is_env_allowed_from_host(key)}

        task_env_keys = frozenset(exec_plan.env)
        env.update(exec_plan.env)

        if exec_plan.plugin_name == "rule":
            env = _filter_rule_application_env(env)
        elif exec_plan.plugin_name == "spider":
            env = filter_spider_control_env(env)

        for key in list(env.keys()):
            if _is_env_forbidden(key, exec_plan.plugin_name, task_env_keys):
                env.pop(key, None)

        env["PATH"] = authoritative_path(exec_plan.cwd, runtime_handle.path)
        env["PYTHONPATH"] = authoritative_python_path(exec_plan, runtime_handle.path)
        env["VIRTUAL_ENV"] = runtime_handle.path

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
        byte_budget = OutputByteBudget(self.config.max_output_bytes)

        stdout_count = 0
        stderr_count = 0

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
            stdout_task = asyncio.create_task(
                read_output_stream(
                    process.stdout,
                    OutputReadOptions(run_id, "stdout", max_lines, log_sink, seq_counter, byte_budget),
                )
            )
            stderr_task = asyncio.create_task(
                read_output_stream(
                    process.stderr,
                    OutputReadOptions(run_id, "stderr", max_lines, log_sink, seq_counter, byte_budget),
                )
            )
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
                    self._grace_period(process_info.exec_plan),
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
                self._grace_period(process_info.exec_plan),
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
                self._grace_period(process_info.exec_plan),
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
            signal_process_group(process, signal.SIGTERM)
            logger.debug(f"发送 SIGTERM: {process_info.run_id}")

            try:
                await asyncio.wait_for(process.wait(), timeout=grace_period)
            except TimeoutError:
                # 发送 SIGKILL 到整个进程组
                signal_process_group(process, signal.SIGKILL)
                logger.debug(f"发送 SIGKILL: {process_info.run_id}")
                await process.wait()

        except ProcessLookupError:
            # 进程已退出
            pass
        except Exception as e:
            # P0-04 (round6): 终止失败必须向上抛,让调用方(engine.cancel /
            # forced_cancel)知道 kill 未生效,不能报告"取消成功"给 Master;
            # 否则旧子进程与 L2 接管者并行执行,PEL reclaim 双执行。
            logger.error(f"终止进程失败(将向上抛让 caller 感知): run_id={process_info.run_id} err={e}")
            raise RuntimeError(f"终止进程失败: run_id={process_info.run_id}") from e

        # P0-04 (round6): SIGKILL + process.wait() 后如果 returncode 仍是 None,
        # 说明子进程未死透(极端情况:D-state / kernel bug / cgroup 冻结),
        # 这时也必须让 caller 知道,不能沉默返回。
        if process.returncode is None:
            raise RuntimeError(f"终止进程失败: 发送 SIGKILL 后进程仍未退出: run_id={process_info.run_id}")

    async def _monitor_resources(self, process_info: ProcessInfo) -> None:
        """按整棵进程树监控 CPU/内存，超限时主动杀掉进程组。

        B8: 采样必须走 ``sample_process_tree``——启用沙箱时 ``process.pid`` 只是
        bwrap 外壳（rule 场景外面还有一层 relay），真正吃内存的是它的子孙进程，
        只看根进程的 RSS 会让超限判定永不成立。

        采样异常一律上抛：监控失效意味着资源上限失去主动兜底，调用方必须知情，
        不能把这次执行悄悄报成 SUCCESS。
        """
        process = process_info.process
        exec_plan = process_info.exec_plan

        try:
            root = psutil.Process(process.pid)
        except (psutil.NoSuchProcess, ProcessLookupError):
            return

        interval = self._get_monitor_interval(exec_plan)

        while process.returncode is None:
            usage = sample_process_tree(root)
            if usage is None:
                return

            process_info.cpu_time_seconds = usage.cpu_time_seconds
            process_info.memory_peak_mb = max(process_info.memory_peak_mb, usage.memory_rss_mb)

            # rlimit 是硬兜底，主动监控负责在失控前提前杀掉整个进程组
            breach = _describe_limit_breach(usage, exec_plan)
            if breach is not None:
                logger.warning(f"{breach}, run_id={process_info.run_id}, 进程树成员数={usage.process_count}")
                signal_process_group(process, signal.SIGKILL)
                return

            await asyncio.sleep(interval)

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
        """把退出码与已采样的资源用量翻译成 (status, exit_reason, error_message)。"""
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

        # 超限判定必须排在"被信号杀死"之前：内存上限主要由 _monitor_resources
        # 主动 SIGKILL 进程树来兑现（RLIMIT_DATA 不覆盖 MAP_SHARED 与 tmpfs），
        # 退出码恒为 -9，先匹配 KILLED 会把"内存超限"抹成笼统的"进程被终止"。
        breach = _limit_breach_result(process_info)
        if breach is not None:
            return breach

        if exit_code in (-signal.SIGTERM, -signal.SIGKILL, -15, -9):
            return RunStatus.KILLED, ExitReason.KILLED, "进程被终止"

        return RunStatus.FAILED, ExitReason.ERROR, f"退出码: {exit_code}"

    async def _do_cancel(self, run_id: str, task_info: Any) -> None:
        """执行取消操作"""
        process_info: ProcessInfo = task_info
        process_info.cancelled = True

        await self._terminate_process(
            process_info,
            self._grace_period(process_info.exec_plan),
        )
