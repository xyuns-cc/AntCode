"""Worker component shutdown orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class ShutdownStep:
    label: str
    operation: Callable[[], Awaitable[None]]


async def shutdown_components(container: Any, grace_period: float, hooks: Iterable[Callable]) -> None:
    """Run every cleanup step in dependency order and aggregate failures."""
    steps = (
        ShutdownStep("关闭钩子", lambda: _run_hooks(hooks)),
        ShutdownStep("引擎", lambda: _stop_engine(container.engine, grace_period)),
        ShutdownStep("Lease 撤销", lambda: _deregister(container.transport)),
        ShutdownStep("心跳上报", lambda: _stop_component(container.heartbeat_reporter)),
        ShutdownStep("执行器", lambda: _stop_component(container.executor)),
        ShutdownStep("运行时管理器", lambda: _stop_component(container.runtime_manager)),
        ShutdownStep("可观测性服务", lambda: _stop_component(container.observability_server)),
        ShutdownStep("传输层", lambda: _stop_transport(container.transport)),
    )
    await _run_steps(steps)


async def _run_steps(steps: Iterable[ShutdownStep]) -> None:
    failures: list[Exception] = []
    for step in steps:
        try:
            await step.operation()
            logger.info(f"{step.label}已停止")
        except Exception as exc:
            logger.opt(exception=exc).error("{}停止失败", step.label)
            failures.append(exc)
    if failures:
        raise ExceptionGroup("Worker 关闭阶段失败", failures)


async def _run_hooks(hooks: Iterable[Callable]) -> None:
    failures: list[Exception] = []
    for index, hook in enumerate(hooks):
        try:
            await _invoke_hook(hook)
        except Exception as exc:
            logger.opt(exception=exc).error("关闭钩子[{}]执行失败", index)
            failures.append(exc)
    if failures:
        raise ExceptionGroup("Worker 关闭钩子失败", failures)


async def _invoke_hook(hook: Callable) -> None:
    result = hook()
    if isinstance(result, Awaitable):
        await result


async def _stop_engine(engine: Any, grace_period: float) -> None:
    stop = getattr(engine, "stop", None)
    if callable(stop):
        await stop(grace_period=grace_period)


async def _deregister(transport: Any) -> None:
    deregister = getattr(transport, "deregister", None)
    if callable(deregister):
        await deregister("worker_shutdown")


async def _stop_component(component: Any) -> None:
    stop = getattr(component, "stop", None)
    if callable(stop):
        await stop()


async def _stop_transport(transport: Any) -> None:
    stop = getattr(transport, "stop", None)
    if callable(stop):
        await stop(grace_period=5.0)


__all__ = ["shutdown_components"]
