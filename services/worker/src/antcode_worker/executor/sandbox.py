"""
沙箱模块

提供可插拔的沙箱实现，支持 no-op 模式。

Requirements: 7.4
"""

import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
from antcode_worker.executor.process import ProcessExecutor


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
    def wrap_command(
        self, cmd: list[str], context: dict[str, Any]
    ) -> list[str]:
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
    def filter_env(
        self, env: dict[str, str], context: dict[str, Any]
    ) -> dict[str, str]:
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

    def wrap_command(
        self, cmd: list[str], context: dict[str, Any]
    ) -> list[str]:
        """不包装命令"""
        return cmd

    def filter_env(
        self, env: dict[str, str], context: dict[str, Any]
    ) -> dict[str, str]:
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
        }

        # 如果启用文件系统隔离，创建临时工作目录
        if self.config.fs_isolated:
            temp_base = self.config.temp_dir or tempfile.gettempdir()
            temp_work_dir = os.path.join(
                temp_base, f"sandbox_{os.getpid()}_{id(exec_plan)}"
            )
            os.makedirs(temp_work_dir, exist_ok=True)
            context["temp_work_dir"] = temp_work_dir
            context["cleanup_dirs"].append(temp_work_dir)
            context["work_dir"] = temp_work_dir
        else:
            context["work_dir"] = work_dir

        return context

    def wrap_command(
        self, cmd: list[str], context: dict[str, Any]
    ) -> list[str]:
        """包装命令"""
        # 如果配置了自定义沙箱命令，使用它
        if self.config.sandbox_command:
            return self.config.sandbox_command + cmd
        return cmd

    def filter_env(
        self, env: dict[str, str], context: dict[str, Any]
    ) -> dict[str, str]:
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

        # 只保留允许的环境变量，同时过滤敏感信息
        for key in self.config.allowed_env_vars:
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
        run_id = exec_plan.plugin_name or f"sandbox_{id(exec_plan)}"

        # 获取信号量
        async with self._semaphore:
            return await self._execute_in_sandbox(
                run_id, exec_plan, runtime_handle, sink
            )

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

        try:
            # 准备沙箱环境
            work_dir = exec_plan.cwd or runtime_handle.path
            context = await self._sandbox.prepare(exec_plan, work_dir)

            # 创建沙箱化的执行计划
            sandboxed_plan = self._create_sandboxed_plan(
                exec_plan, runtime_handle, context
            )

            # 使用 ProcessExecutor 执行
            result = await self._process_executor.run(
                sandboxed_plan, runtime_handle, log_sink
            )

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
            # 清理沙箱环境
            if context:
                await self._sandbox.cleanup(context)

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

        # 包装命令
        wrapped_cmd = self._sandbox.wrap_command(cmd, context)

        # 过滤环境变量
        env = os.environ.copy()
        env["PYTHONPATH"] = runtime_handle.path
        env["VIRTUAL_ENV"] = runtime_handle.path
        env.update(exec_plan.env)
        filtered_env = self._sandbox.filter_env(env, context)

        # 创建新的执行计划
        return ExecPlan(
            command=wrapped_cmd[0],
            args=wrapped_cmd[1:],
            env=filtered_env,
            cwd=context.get("work_dir", exec_plan.cwd),
            timeout_seconds=exec_plan.timeout_seconds,
            grace_period_seconds=exec_plan.grace_period_seconds,
            memory_limit_mb=exec_plan.memory_limit_mb,
            cpu_limit_seconds=exec_plan.cpu_limit_seconds,
            artifact_patterns=exec_plan.artifact_patterns,
            collect_stdout=exec_plan.collect_stdout,
            collect_stderr=exec_plan.collect_stderr,
            sandbox_enabled=False,  # 已经在沙箱中
            plugin_name=exec_plan.plugin_name,
        )

    async def _do_cancel(self, run_id: str, task_info: Any) -> None:
        """执行取消操作"""
        # 委托给 ProcessExecutor
        await self._process_executor.cancel(run_id)


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
