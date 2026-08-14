"""Factories for Worker sandbox providers and executors."""

from antcode_worker.executor.base import ExecutorConfig
from antcode_worker.executor.sandbox_config import SandboxConfig
from antcode_worker.executor.sandbox_provider import BasicSandbox, NoOpSandbox, SandboxProvider


def create_sandbox(
    sandbox_type: str = "basic",
    config: SandboxConfig | None = None,
) -> SandboxProvider:
    """Create the explicitly selected sandbox provider."""
    if sandbox_type == "noop":
        return NoOpSandbox()
    if sandbox_type == "basic":
        return BasicSandbox(config or SandboxConfig())
    raise ValueError(f"未知的沙箱类型: {sandbox_type}")


def create_sandbox_executor(
    executor_config: ExecutorConfig | None = None,
    sandbox_config: SandboxConfig | None = None,
    sandbox_type: str = "basic",
):
    """Create a SandboxExecutor without introducing an import cycle."""
    from antcode_worker.executor.sandbox import SandboxExecutor

    sandbox = create_sandbox(sandbox_type, sandbox_config)
    return SandboxExecutor(
        config=executor_config,
        sandbox_config=sandbox_config,
        sandbox_provider=sandbox,
    )


__all__ = ["create_sandbox", "create_sandbox_executor"]
