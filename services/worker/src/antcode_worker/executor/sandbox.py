"""
沙箱模块

提供可插拔的沙箱实现，支持 no-op 模式。

Requirements: 7.4
"""

import asyncio
import contextlib
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from antcode_worker.config import DATA_ROOT
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
from antcode_worker.executor.process import ProcessExecutor
from antcode_worker.executor.rule_policy import (
    RULE_PLUGIN_ENV_VARS,
    RULE_SANDBOX_ALLOW_NETWORK,
)

_RULE_PLUGIN_ENV_VARS = RULE_PLUGIN_ENV_VARS
_PRLIMIT_EXECUTABLE = "prlimit"
_PAYLOAD_PROCESS_LIMIT_KEY = "payload_max_processes"


@dataclass
class SandboxConfig:
    """
    沙箱配置

    Requirements: 7.4
    """

    # 是否启用沙箱
    enabled: bool = True

    # 是否启用网络隔离
    network_isolated: bool = False

    # 是否启用文件系统隔离
    fs_isolated: bool = True

    # 允许的环境变量
    allowed_env_vars: list[str] = field(
        default_factory=lambda: [
            "PATH",
            "HOME",
            "PYTHONPATH",
            "LANG",
            "LC_ALL",
            "VIRTUAL_ENV",
            "UV_CACHE_DIR",
            "GOCACHE",
            "GOMODCACHE",
            "GOENV",
            "GOTOOLCHAIN",
            # P2 §4.4: 沙箱内 go 必须离线（GOPROXY=off）使用 prep 阶段
            # 预热的模块缓存；GOFLAGS=-mod=mod 允许按 go.mod 读缓存。
            "GOPROXY",
            "GOFLAGS",
        ]
    )

    # 临时目录
    temp_dir: str | None = None

    # 最大文件大小（字节）
    max_file_size: int = 100 * 1024 * 1024  # 100MB

    # 最大输出大小（字节）
    max_output_size: int = 10 * 1024 * 1024  # 10MB

    # 是否清理工作目录
    cleanup_on_exit: bool = True

    # 自定义沙箱命令前缀（如 firejail, bubblewrap 等）
    sandbox_command: list[str] | None = None


@dataclass
class SandboxRunMarker:
    """连接沙箱准备阶段与内层进程启动阶段的取消状态。"""

    cancel_requested: bool = False


class SandboxProvider(ABC):
    """
    沙箱提供者抽象基类

    定义沙箱的接口，支持不同的沙箱实现。

    Requirements: 7.4
    """

    @abstractmethod
    async def prepare(self, exec_plan: ExecPlan, work_dir: str) -> dict[str, Any]:
        """
        准备沙箱环境

        Args:
            exec_plan: 执行计划
            work_dir: 工作目录

        Returns:
            沙箱上下文（传递给 wrap_command 和 cleanup）
        """
        pass

    @abstractmethod
    def wrap_command(self, cmd: list[str], context: dict[str, Any]) -> list[str]:
        """
        包装命令以在沙箱中执行

        Args:
            cmd: 原始命令
            context: 沙箱上下文

        Returns:
            包装后的命令
        """
        pass

    @abstractmethod
    def filter_env(self, env: dict[str, str], context: dict[str, Any]) -> dict[str, str]:
        """
        过滤环境变量

        Args:
            env: 原始环境变量
            context: 沙箱上下文

        Returns:
            过滤后的环境变量
        """
        pass

    @abstractmethod
    async def cleanup(self, context: dict[str, Any]) -> None:
        """
        清理沙箱环境

        Args:
            context: 沙箱上下文
        """
        pass


class NoOpSandbox(SandboxProvider):
    """
    空操作沙箱

    不做任何隔离，直接执行命令。

    Requirements: 7.4
    """

    async def prepare(self, exec_plan: ExecPlan, work_dir: str) -> dict[str, Any]:
        """准备（无操作）"""
        return {"work_dir": work_dir}

    def wrap_command(self, cmd: list[str], context: dict[str, Any]) -> list[str]:
        """不包装命令"""
        return cmd

    def filter_env(self, env: dict[str, str], context: dict[str, Any]) -> dict[str, str]:
        """不过滤环境变量"""
        return env

    async def cleanup(self, context: dict[str, Any]) -> None:
        """清理（无操作）"""
        pass


class BasicSandbox(SandboxProvider):
    """
    基础沙箱

    提供基本的隔离功能：
    - 环境变量过滤
    - 临时工作目录
    - 输出大小限制

    Requirements: 7.4
    """

    def __init__(self, config: SandboxConfig):
        """
        初始化基础沙箱

        Args:
            config: 沙箱配置
        """
        self.config = config

    async def prepare(self, exec_plan: ExecPlan, work_dir: str) -> dict[str, Any]:
        """准备沙箱环境"""
        context: dict[str, Any] = {
            "original_work_dir": work_dir,
            "temp_work_dir": None,
            "cleanup_dirs": [],
            "plugin_name": exec_plan.plugin_name,
            "allow_network": self._rule_network_allowed(exec_plan),
        }

        # 外部 namespace 沙箱直接绑定当前 run 的独立 workspace。创建空临时
        # cwd 会让相对资源和源码入口不可见，因此只在无外部沙箱的测试 provider
        # 中保留临时目录行为。
        if self.config.fs_isolated and not self.config.sandbox_command:
            temp_base = self.config.temp_dir or str(DATA_ROOT / "temp" / "sandbox")
            os.makedirs(temp_base, exist_ok=True)
            temp_work_dir = os.path.join(temp_base, f"sandbox_{os.getpid()}_{id(exec_plan)}")
            os.makedirs(temp_work_dir, exist_ok=True)
            context["temp_work_dir"] = temp_work_dir
            context["cleanup_dirs"].append(temp_work_dir)
            context["work_dir"] = temp_work_dir
        else:
            context["work_dir"] = work_dir

        return context

    def wrap_command(self, cmd: list[str], context: dict[str, Any]) -> list[str]:
        """包装命令"""
        if not self.config.sandbox_command:
            raise RuntimeError("真实沙箱命令未配置，拒绝执行用户任务")

        # P0-01: 沙箱启动器必须是绝对路径（wiring 已 shutil.which 解析）；defense-in-depth 断言防绕过 wiring 的相对路径被 env.PATH 劫持。
        if not os.path.isabs(self.config.sandbox_command[0]):
            raise RuntimeError(
                f"沙箱启动命令必须是绝对路径,当前 {self.config.sandbox_command[0]!r} 会被任务 env.PATH 劫持"
            )

        executable = os.path.basename(self.config.sandbox_command[0])
        # P0-04 (round6): 只允许 bwrap。原实现对任何非 bwrap 名的绝对命令直接前缀
        # 执行(如 WORKER_SANDBOX_COMMAND=/usr/bin/env),相当于绕过所有 --unshare-*
        # 隔离,配置错误就把整个沙箱语义变成空壳。firejail 等其他隔离器暂不支持,
        # 显式拒绝而不是静默前缀。若需要扩展,应在下面 else 分支加对应 wrap 逻辑。
        if executable != "bwrap":
            raise RuntimeError(
                f"仅支持 bwrap 沙箱,拒绝执行未知启动器 {executable!r}; 其他前缀命令(如 /usr/bin/env)会绕过所有隔离语义"
            )

        work_dir = str(context["work_dir"])
        wrapped = [
            *self.config.sandbox_command,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
        ]
        if self.config.network_isolated and not context.get("allow_network", False):
            wrapped.append("--unshare-net")

        sensitive_dirs = (
            DATA_ROOT / "secrets",
            DATA_ROOT / "identity",
            DATA_ROOT / "runs",
            DATA_ROOT / "temp",
        )
        for sensitive_dir in sensitive_dirs:
            if sensitive_dir.exists():
                wrapped.extend(["--tmpfs", str(sensitive_dir)])

        # 主机凭据目录 masking: --ro-bind / / 会把整棵主机 FS 只读暴露给不可信
        # 载荷,凭据目录必须额外用 tmpfs 盖掉。载荷/运行时(uv/go/python/
        # playwright,缓存均在 DATA_ROOT / /opt / /app)从不需要这些目录,tmpfs
        # 后仍可写只是不映射主机内容;.exists() 守卫保证目录缺失时不下发挂载点
        # (避免 ro 根下 bwrap 建挂载点失败)。仅盖凭据子目录,不整体盖 $HOME/
        # /root,防止误伤潜在缓存。
        for cred_dir in self._credential_mask_dirs():
            wrapped.extend(["--tmpfs", str(cred_dir)])

        self._append_mount_dirs(wrapped, Path(work_dir), sensitive_dirs)

        wrapped.extend(["--bind", work_dir, work_dir, "--chdir", work_dir, "--"])
        return [*wrapped, *self._limit_payload_processes(cmd, context)]

    @staticmethod
    def _credential_mask_dirs() -> list[Path]:
        """返回需要用 tmpfs 盖掉的主机凭据目录(仅返回真实存在的目录)。

        ``--ro-bind / /`` 把整棵主机 FS 只读暴露给载荷,这里挑出常见凭据目录额外
        tmpfs 遮蔽。要点:
        - 用 ``.is_dir()`` 而非 ``.exists()`` 过滤:``--tmpfs`` 只能挂在目录挂载点上,
          对普通文件下 tmpfs 会让 bwrap 直接失败,导致所有任务无法启动。
        - 返回 ``resolve()`` 后的**真实路径**而非原候选路径:若 ``~/.aws`` 是指向
          ``/root/.aws`` 的符号链接,只盖符号链接会留下真实目录经 ``--ro-bind / /``
          仍可读(屏蔽被绕过);盖真实路径则符号链接访问也一并落空。
        - 按真实路径去重(HOME 可能就是 /root,或多个候选指向同一目录)。
        """
        candidates = [
            Path.home() / ".ssh",
            Path.home() / ".aws",
            Path.home() / ".config" / "gcloud",
            Path.home() / ".kube",
            Path.home() / ".docker",
            Path("/root/.ssh"),
            Path("/root/.aws"),
            # P0-03: Docker/K8s Secret 挂载点（/run/secrets, /var/run/secrets/kubernetes.io, /etc/kubernetes, /var/lib/kubelet），一律 tmpfs 掩掉防 SA token 泄漏给用户载荷。
            Path("/run/secrets"),
            Path("/var/run/secrets/kubernetes.io"),
            Path("/etc/kubernetes"),
            Path("/var/lib/kubelet"),
            # P0-04 (round6): Worker 自身身份 + mTLS 私钥 + 运行时数据目录。
            # bwrap --ro-bind / / 会把 Worker 进程可见的凭据文件都暴露给任务;
            # 这些目录一律 tmpfs 掩掉,任务不能通过读文件或 argv 外带凭据。
            # 具体覆盖:
            #  - /etc/antcode/tls     (Gateway/Web API mTLS 证书私钥挂载点)
            #  - /etc/antcode          (Worker YAML 配置 + Direct Redis URL)
            #  - /app/data/worker      (Worker identity 存储目录; secrets/, runtime_data/)
            #  - /var/lib/antcode      (K8s 时代备用挂载点,即使退役也保持防御)
            Path("/etc/antcode/tls"),
            Path("/etc/antcode"),
            Path("/app/data/worker"),
            Path("/var/lib/antcode"),
        ]
        seen: set[Path] = set()
        masked: list[Path] = []
        for candidate in candidates:
            try:
                if not candidate.is_dir():  # is_dir() 跟随符号链接,同时排除文件
                    continue
                resolved = candidate.resolve()
                if not resolved.is_dir():
                    continue
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            masked.append(resolved)
        return masked

    @staticmethod
    def _limit_payload_processes(cmd: list[str], context: dict[str, Any]) -> list[str]:
        limit = int(context.get(_PAYLOAD_PROCESS_LIMIT_KEY, 0))
        if limit <= 0:
            return cmd
        prlimit = shutil.which(_PRLIMIT_EXECUTABLE)
        if not prlimit:
            raise RuntimeError("启用 bwrap 进程限制需要 prlimit")
        return [prlimit, f"--nproc={limit}:{limit}", "--", *cmd]

    @staticmethod
    def _rule_network_allowed(exec_plan: ExecPlan) -> bool:
        if exec_plan.plugin_name != "rule":
            return False
        return exec_plan.sandbox_config.get(RULE_SANDBOX_ALLOW_NETWORK) is True

    @staticmethod
    def _append_mount_dirs(
        wrapped: list[str],
        work_dir: Path,
        sensitive_dirs: tuple[Path, ...],
    ) -> None:
        resolved = work_dir.resolve()
        masked_roots = (Path("/tmp"), *sensitive_dirs)
        for root in masked_roots:
            try:
                relative = resolved.relative_to(root.resolve())
            except ValueError:
                continue
            current = root
            for part in relative.parts:
                current /= part
                wrapped.extend(["--dir", str(current)])
            return

    def filter_env(self, env: dict[str, str], context: dict[str, Any]) -> dict[str, str]:
        """过滤环境变量"""
        filtered = {}

        # 敏感信息关键词
        sensitive_patterns = {
            "SECRET",
            "PASSWORD",
            "TOKEN",
            "API_KEY",
            "CREDENTIAL",
            "PRIVATE",
        }

        plugin_name = context.get("plugin_name")
        allowed_env_vars = set(self.config.allowed_env_vars)
        if plugin_name == "rule":
            allowed_env_vars.update(_RULE_PLUGIN_ENV_VARS)

        # 只保留允许的环境变量，同时过滤敏感信息
        for key in allowed_env_vars:
            if key in env:
                key_upper = key.upper()
                is_sensitive = any(p in key_upper for p in sensitive_patterns)
                if not is_sensitive:
                    filtered[key] = env[key]

        return filtered

    async def cleanup(self, context: dict[str, Any]) -> None:
        """清理沙箱环境"""
        if not self.config.cleanup_on_exit:
            return

        for dir_path in context.get("cleanup_dirs", []):
            try:
                if os.path.exists(dir_path):
                    shutil.rmtree(dir_path)
                    logger.debug(f"已清理沙箱目录: {dir_path}")
            except Exception as e:
                logger.warning(f"清理沙箱目录失败: {dir_path}, error={e}")


class SandboxExecutor(BaseExecutor):
    """
    沙箱执行器

    在沙箱环境中执行任务，支持可插拔的沙箱实现。

    Requirements: 7.4
    """

    def __init__(
        self,
        config: ExecutorConfig | None = None,
        sandbox_config: SandboxConfig | None = None,
        sandbox_provider: SandboxProvider | None = None,
    ):
        """
        初始化沙箱执行器

        Args:
            config: 执行器配置
            sandbox_config: 沙箱配置
            sandbox_provider: 沙箱提供者（可选，默认使用 BasicSandbox）
        """
        super().__init__(config)

        self.sandbox_config = sandbox_config or SandboxConfig()

        # 选择沙箱提供者
        if sandbox_provider:
            self._sandbox = sandbox_provider
        elif self.sandbox_config.enabled:
            self._sandbox = BasicSandbox(self.sandbox_config)
        else:
            self._sandbox = NoOpSandbox()

        # 内部使用 ProcessExecutor 执行
        self._process_executor = ProcessExecutor(config)

    @property
    def sandbox(self) -> SandboxProvider:
        """获取沙箱提供者"""
        return self._sandbox

    async def start(self) -> None:
        """启动执行器"""
        await super().start()
        await self._process_executor.start()

    async def stop(self, grace_period: float = 10.0) -> None:
        """停止执行器"""
        await self._process_executor.stop(grace_period)
        await super().stop(grace_period)

    async def run(
        self,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink | None = None,
    ) -> ExecResult:
        """
        在沙箱中执行任务

        Args:
            exec_plan: 执行计划
            runtime_handle: 运行时句柄
            log_sink: 日志接收器

        Returns:
            ExecResult 执行结果

        Requirements: 7.4
        """
        sink = log_sink or NoOpLogSink()
        # P0-02: 用真实 run_id 注册任务；plugin_name 是共享键（"rule"/"code"），
        # 会让 base.cancel() 找不到真 run_id 而直接返回 False，也会让同插件并发任务互相覆盖。
        run_id = exec_plan.run_id or exec_plan.plugin_name or f"sandbox_{id(exec_plan)}"

        # 获取信号量
        async with self._semaphore:
            return await self._execute_in_sandbox(run_id, exec_plan, runtime_handle, sink)

    async def _execute_in_sandbox(
        self,
        run_id: str,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        log_sink: LogSink,
    ) -> ExecResult:
        """在沙箱中执行任务"""
        started_at = datetime.now()
        context: dict[str, Any] = {}
        process_task: asyncio.Task[ExecResult] | None = None

        # P0-02: 外层用真 run_id 注册一个哨兵，让 BaseExecutor.cancel(run_id) 能命中并走到 _do_cancel。
        # 真实进程句柄在内层 ProcessExecutor 中，取消时委托到 self._process_executor.cancel(run_id)。
        marker = SandboxRunMarker()
        await self._register_task(run_id, marker)

        try:
            # 准备沙箱环境
            work_dir = exec_plan.cwd or runtime_handle.path
            context = await self._sandbox.prepare(exec_plan, work_dir)

            # 创建沙箱化的执行计划
            sandboxed_plan = self._create_sandboxed_plan(exec_plan, runtime_handle, context)

            if marker.cancel_requested:
                result = self._cancelled_before_process_start(run_id, started_at)
                self._update_stats(result.status)
                return result

            startup_event = asyncio.Event()
            process_task = asyncio.create_task(
                self._process_executor.run(
                    sandboxed_plan,
                    runtime_handle,
                    log_sink,
                    startup_event=startup_event,
                )
            )
            await startup_event.wait()
            if marker.cancel_requested:
                await self._process_executor.cancel(run_id)
            result = await process_task

            # 更新统计
            self._update_stats(result.status)

            return result

        except Exception as e:
            logger.error(f"沙箱执行异常: {run_id}, error={e}")

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

        finally:
            await self._cancel_pending_process_task(process_task)
            # 清理沙箱环境
            if context:
                await self._sandbox.cleanup(context)
            # P0-02: 无论成功/失败/异常都要注销，避免 _running_tasks 泄漏
            await self._unregister_task(run_id)

    def _create_sandboxed_plan(
        self,
        exec_plan: ExecPlan,
        runtime_handle: RuntimeHandle,
        context: dict[str, Any],
    ) -> ExecPlan:
        """创建沙箱化的执行计划"""
        # 构建原始命令
        if exec_plan.command.endswith(".py"):
            cmd = [runtime_handle.python_executable, exec_plan.command]
        else:
            cmd = [exec_plan.command]
        cmd.extend(exec_plan.args)

        context[_PAYLOAD_PROCESS_LIMIT_KEY] = self._payload_process_limit(exec_plan)

        # 包装命令
        wrapped_cmd = self._sandbox.wrap_command(cmd, context)

        # 过滤环境变量
        # P0-01: 环境合并顺序 = os.environ → exec_plan.env → filter_env(白名单) → Worker 权威强写 PYTHONPATH/VIRTUAL_ENV/PATH，并剥离 LD_*/BASH_ENV/ENV 劫持向量。
        env = os.environ.copy()
        env.update(exec_plan.env)
        filtered_env = self._sandbox.filter_env(env, context)
        filtered_env["PYTHONPATH"] = runtime_handle.path
        filtered_env["VIRTUAL_ENV"] = runtime_handle.path
        filtered_env["PATH"] = os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        for hijack_key in ("LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "BASH_ENV", "ENV"):
            filtered_env.pop(hijack_key, None)

        # P0-02: 创建执行计划必须原样透传 run_id + 全部资源/rlimit 字段，避免 ProcessExecutor 退回 plugin_name 共享键导致 cancel 失联 + rlimit 被丢弃。
        return ExecPlan(
            command=wrapped_cmd[0],
            args=wrapped_cmd[1:],
            env=filtered_env,
            cwd=context.get("work_dir", exec_plan.cwd),
            run_id=exec_plan.run_id,
            timeout_seconds=exec_plan.timeout_seconds,
            grace_period_seconds=exec_plan.grace_period_seconds,
            memory_limit_mb=exec_plan.memory_limit_mb,
            cpu_limit_seconds=exec_plan.cpu_limit_seconds,
            max_open_files=exec_plan.max_open_files,
            max_processes=exec_plan.max_processes,
            max_file_size_mb=exec_plan.max_file_size_mb,
            enforce_rlimit=exec_plan.enforce_rlimit,
            artifact_patterns=exec_plan.artifact_patterns,
            collect_stdout=exec_plan.collect_stdout,
            collect_stderr=exec_plan.collect_stderr,
            sandbox_enabled=False,  # 已经在沙箱中
            sandbox_config=dict(exec_plan.sandbox_config),
            plugin_name=exec_plan.plugin_name,
        )

    def _payload_process_limit(self, exec_plan: ExecPlan) -> int:
        enforce = exec_plan.enforce_rlimit if exec_plan.enforce_rlimit is not None else self.config.enforce_rlimit
        if not enforce:
            return 0
        return exec_plan.max_processes or self.config.default_max_processes

    async def _do_cancel(self, run_id: str, task_info: Any) -> None:
        """执行取消操作"""
        if isinstance(task_info, SandboxRunMarker):
            task_info.cancel_requested = True
        await self._process_executor.cancel(run_id)

    def _cancelled_before_process_start(self, run_id: str, started_at: datetime) -> ExecResult:
        return self._create_result(
            run_id=run_id,
            status=RunStatus.CANCELLED,
            exit_reason=ExitReason.CANCELLED,
            error_message="任务在沙箱准备阶段被取消",
            started_at=started_at,
            finished_at=datetime.now(),
        )

    @staticmethod
    async def _cancel_pending_process_task(process_task: asyncio.Task[ExecResult] | None) -> None:
        if process_task is None or process_task.done():
            return
        process_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await process_task


# 沙箱工厂函数


def create_sandbox(
    sandbox_type: str = "basic",
    config: SandboxConfig | None = None,
) -> SandboxProvider:
    """
    创建沙箱提供者

    Args:
        sandbox_type: 沙箱类型 ("noop", "basic")
        config: 沙箱配置

    Returns:
        SandboxProvider 实例
    """
    if sandbox_type == "noop":
        return NoOpSandbox()
    elif sandbox_type == "basic":
        return BasicSandbox(config or SandboxConfig())
    else:
        raise ValueError(f"未知的沙箱类型: {sandbox_type}")


def create_sandbox_executor(
    executor_config: ExecutorConfig | None = None,
    sandbox_config: SandboxConfig | None = None,
    sandbox_type: str = "basic",
) -> SandboxExecutor:
    """
    创建沙箱执行器

    Args:
        executor_config: 执行器配置
        sandbox_config: 沙箱配置
        sandbox_type: 沙箱类型

    Returns:
        SandboxExecutor 实例
    """
    sandbox = create_sandbox(sandbox_type, sandbox_config)
    return SandboxExecutor(
        config=executor_config,
        sandbox_config=sandbox_config,
        sandbox_provider=sandbox,
    )
