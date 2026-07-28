"""
P0-02 回归测试：SandboxExecutor 必须按真实 run_id 注册与取消。

背景（GPT 2026-07-13 审查报告 P0-02）：
- 生产默认 sandbox_mode=sandbox（wiring.py），SandboxExecutor 是生产执行器；
- 但 SandboxExecutor.run() 之前用 `exec_plan.plugin_name` 而非真实 run_id 作为
  执行 key，且从不调用 _register_task；
- 结果：BaseExecutor.cancel(真run_id) 首行查 _running_tasks 直接 return False，
  取消不会进入 _do_cancel；同插件（如 rule/code）并发任务用共享键覆盖注册；
- 界面显示 CANCELLED，真实进程照跑。

本测试证明修复后：
1. run 期间 SandboxExecutor._running_tasks 里挂着真 run_id 的哨兵；
2. cancel(真run_id) 返回 True 并委托到内层 ProcessExecutor.cancel(真run_id)；
3. 内层重建 ExecPlan 时透传了 run_id，不会退回 plugin_name 共享键；
4. 同插件双并发任务 A、B 时，cancel(A.run_id) 不会误停 B。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

worker_src = Path(__file__).parent.parent.parent.parent / "services" / "worker" / "src"
if str(worker_src) not in sys.path:
    sys.path.insert(0, str(worker_src))

from antcode_worker.domain.enums import ExitReason, RunStatus  # noqa: E402
from antcode_worker.domain.models import ExecPlan, ExecResult, RuntimeHandle  # noqa: E402
from antcode_worker.executor.base import ExecutorConfig, NoOpLogSink  # noqa: E402
from antcode_worker.executor.sandbox import (  # noqa: E402
    NoOpSandbox,
    SandboxConfig,
    SandboxExecutor,
)


def _plan(run_id: str, plugin: str = "rule") -> ExecPlan:
    return ExecPlan(
        command="/bin/true",
        args=[],
        run_id=run_id,
        cwd="/tmp",
        timeout_seconds=5,
        plugin_name=plugin,
    )


def _runtime() -> RuntimeHandle:
    return RuntimeHandle(
        path="/tmp/venv",
        runtime_hash="sha256:testtesttesttest",
        python_executable="/usr/bin/python3",
    )


@pytest.mark.asyncio
async def test_sandbox_registers_real_run_id_during_run() -> None:
    """
    P0-02：SandboxExecutor.run() 期间必须以真 run_id 挂入 _running_tasks，
    这样 BaseExecutor.cancel(真run_id) 才能命中并走到 _do_cancel。
    """
    executor = SandboxExecutor(
        config=ExecutorConfig(max_concurrent=4),
        sandbox_config=SandboxConfig(enabled=True, fs_isolated=False),
        sandbox_provider=NoOpSandbox(),
    )

    run_id = "run-real-abc"
    plan = _plan(run_id)

    # 冒充内层 ProcessExecutor 的执行流程，把控制权交给测试
    gate = asyncio.Event()
    captured_plan: dict[str, ExecPlan] = {}

    async def fake_process_run(sandboxed_plan, runtime_handle, sink, *, startup_event=None):
        if startup_event is not None:
            startup_event.set()
        captured_plan["plan"] = sandboxed_plan
        # 挂住等取消
        await gate.wait()
        return ExecResult(
            run_id=sandboxed_plan.run_id or "",
            status=RunStatus.SUCCESS,
            exit_reason=ExitReason.NORMAL,
        )

    executor._process_executor.run = fake_process_run  # type: ignore[method-assign]
    executor._process_executor.cancel = AsyncMock(return_value=True)  # type: ignore[method-assign]

    async def drive_run():
        return await executor.run(plan, _runtime(), NoOpLogSink())

    task = asyncio.create_task(drive_run())
    # 等 run 走到内层挂起
    for _ in range(50):
        if run_id in executor._running_tasks:
            break
        await asyncio.sleep(0.01)

    # 1) 真 run_id 已注册到外层
    assert run_id in executor._running_tasks, (
        "回归失败：SandboxExecutor 未以真 run_id 注册任务，BaseExecutor.cancel(真run_id) 将永远返回 False（P0-02）。"
    )
    # 2) 内层重建 ExecPlan 时必须透传 run_id
    assert captured_plan["plan"].run_id == run_id, (
        "回归失败：_create_sandboxed_plan 未透传 run_id，"
        "内层 ProcessExecutor 会退回用 plugin_name 共享键注册（P0-02）。"
    )
    # 3) cancel(真run_id) 应返回 True 并委托到内层
    ok = await executor.cancel(run_id)
    assert ok is True, "回归失败：cancel(真run_id) 返回 False，取消失效（P0-02）。"
    executor._process_executor.cancel.assert_awaited_with(run_id)  # type: ignore[attr-defined]

    # 收尾：放行内层，等 task 结束，验证注销
    gate.set()
    await task
    assert run_id not in executor._running_tasks, "回归失败：run 结束后未注销 _running_tasks，会泄漏。"


@pytest.mark.asyncio
async def test_sandbox_cancel_wrong_run_id_returns_false() -> None:
    """cancel 不认识的 run_id 时必须 fail-closed 返回 False，不影响活跃任务。"""
    executor = SandboxExecutor(
        config=ExecutorConfig(max_concurrent=2),
        sandbox_config=SandboxConfig(enabled=True, fs_isolated=False),
        sandbox_provider=NoOpSandbox(),
    )

    plan = _plan("run-A", plugin="rule")
    gate = asyncio.Event()

    async def fake_process_run(sandboxed_plan, runtime_handle, sink, *, startup_event=None):
        if startup_event is not None:
            startup_event.set()
        await gate.wait()
        return ExecResult(run_id="run-A", status=RunStatus.SUCCESS, exit_reason=ExitReason.NORMAL)

    executor._process_executor.run = fake_process_run  # type: ignore[method-assign]
    executor._process_executor.cancel = AsyncMock(return_value=True)  # type: ignore[method-assign]

    task = asyncio.create_task(executor.run(plan, _runtime(), NoOpLogSink()))
    for _ in range(50):
        if "run-A" in executor._running_tasks:
            break
        await asyncio.sleep(0.01)

    # 用 plugin_name 当 run_id 尝试取消，必须失败（否则说明又退回共享键了）
    assert await executor.cancel("rule") is False, "回归失败：用 plugin_name 就能取消，退回共享键（P0-02 重现）。"
    # 用错的 run_id 取消也必须失败
    assert await executor.cancel("run-does-not-exist") is False

    gate.set()
    await task


@pytest.mark.asyncio
async def test_sandbox_two_concurrent_same_plugin_cancel_only_target() -> None:
    """
    同插件（plugin_name="rule"）并发两个 run，取消 A 不能误停 B。
    这是原报告最尖锐的场景：共享键让先结束的任务反注销另一个仍在运行的进程。
    """
    executor = SandboxExecutor(
        config=ExecutorConfig(max_concurrent=4),
        sandbox_config=SandboxConfig(enabled=True, fs_isolated=False),
        sandbox_provider=NoOpSandbox(),
    )

    gate_a = asyncio.Event()
    gate_b = asyncio.Event()
    cancel_targets: list[str] = []

    async def fake_process_run(sandboxed_plan, runtime_handle, sink, *, startup_event=None):
        if startup_event is not None:
            startup_event.set()
        if sandboxed_plan.run_id == "run-A":
            await gate_a.wait()
        else:
            await gate_b.wait()
        return ExecResult(
            run_id=sandboxed_plan.run_id or "",
            status=RunStatus.SUCCESS,
            exit_reason=ExitReason.NORMAL,
        )

    async def fake_process_cancel(rid: str) -> bool:
        cancel_targets.append(rid)
        return True

    executor._process_executor.run = fake_process_run  # type: ignore[method-assign]
    executor._process_executor.cancel = fake_process_cancel  # type: ignore[method-assign]

    task_a = asyncio.create_task(executor.run(_plan("run-A", "rule"), _runtime(), NoOpLogSink()))
    task_b = asyncio.create_task(executor.run(_plan("run-B", "rule"), _runtime(), NoOpLogSink()))

    for _ in range(100):
        if "run-A" in executor._running_tasks and "run-B" in executor._running_tasks:
            break
        await asyncio.sleep(0.01)

    assert "run-A" in executor._running_tasks and "run-B" in executor._running_tasks

    # 取消 A：只能命中 A，B 必须仍在 running
    assert await executor.cancel("run-A") is True
    assert cancel_targets == ["run-A"], (
        f"回归失败：取消 A 误传到 {cancel_targets}，说明外层用了共享键注册导致同插件并发互相覆盖（P0-02 重现）。"
    )
    assert "run-B" in executor._running_tasks, "回归失败：B 被误注销"

    gate_a.set()
    gate_b.set()
    await asyncio.gather(task_a, task_b)


class SlowSandbox(NoOpSandbox):
    """由事件控制 prepare 完成时机，不依赖时间轮询。"""

    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self._entered = entered
        self._release = release

    async def prepare(self, exec_plan: ExecPlan, work_dir: str) -> dict[str, object]:
        self._entered.set()
        await self._release.wait()
        return {"work_dir": work_dir}


@pytest.mark.asyncio
async def test_cancel_during_sandbox_prepare_prevents_process_start() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    executor = SandboxExecutor(sandbox_provider=SlowSandbox(entered, release))
    executor._process_executor.run = AsyncMock()  # type: ignore[method-assign]

    task = asyncio.create_task(executor.run(_plan("run-prepare"), _runtime(), NoOpLogSink()))
    await entered.wait()

    assert await executor.cancel("run-prepare") is True
    release.set()
    result = await task

    assert result.status == RunStatus.CANCELLED
    executor._process_executor.run.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancel_while_subprocess_is_starting_terminates_new_process(monkeypatch: pytest.MonkeyPatch) -> None:
    create_entered = asyncio.Event()
    create_release = asyncio.Event()
    real_create = asyncio.create_subprocess_exec

    async def delayed_create(*args, **kwargs):
        create_entered.set()
        await create_release.wait()
        return await real_create(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_create)
    executor = SandboxExecutor(sandbox_provider=NoOpSandbox())
    plan = ExecPlan(
        command="/bin/sleep",
        args=["30"],
        run_id="run-starting",
        cwd="/tmp",
        timeout_seconds=60,
        plugin_name="code",
    )

    task = asyncio.create_task(executor.run(plan, _runtime(), NoOpLogSink()))
    await create_entered.wait()
    assert await executor.cancel("run-starting") is True

    create_release.set()
    result = await task

    assert result.status == RunStatus.CANCELLED
    assert "run-starting" not in executor._running_tasks
    assert "run-starting" not in executor._process_executor._running_tasks
    assert "run-starting" not in executor._process_executor._launch_cancellations
