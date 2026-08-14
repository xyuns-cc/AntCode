"""Worker Engine 核心功能单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_worker.domain.enums import RunStatus
from antcode_worker.domain.models import ExecResult, RunContext, SourceBundle, TaskPayload
from antcode_worker.engine.engine import Engine
from antcode_worker.engine.policies import (
    RetryPolicy,
    default_policies,
)
from antcode_worker.engine.scheduler import Scheduler
from antcode_worker.engine.state import RunState, StateManager

FUTURE_RUNTIME_DEADLINE_MS = 4_102_444_800_000


class TestPolicies:
    """策略测试"""

    def test_default_policies(self):
        """测试默认策略"""
        policies = default_policies()

        assert policies.retry.max_retries == 3
        assert policies.timeout.execution_timeout == 3600
        assert policies.resource.max_concurrent == 5

    def test_retry_policy_delay(self):
        """测试重试延迟计算"""
        policy = RetryPolicy(
            retry_delay=1.0,
            exponential_backoff=True,
            max_delay=60.0,
        )

        assert policy.get_delay(0) == 1.0
        assert policy.get_delay(1) == 2.0
        assert policy.get_delay(2) == 4.0
        assert policy.get_delay(3) == 8.0
        assert policy.get_delay(10) == 60.0  # 受 max_delay 限制

    def test_retry_policy_no_backoff(self):
        """测试无指数退避"""
        policy = RetryPolicy(
            retry_delay=2.0,
            exponential_backoff=False,
        )

        assert policy.get_delay(0) == 2.0
        assert policy.get_delay(5) == 2.0

    def test_retry_policy_should_retry(self):
        """测试是否应该重试"""
        policy = RetryPolicy(max_retries=3)

        assert policy.should_retry(0) is True
        assert policy.should_retry(2) is True
        assert policy.should_retry(3) is False
        assert policy.should_retry(5) is False


class TestScheduler:
    """调度器测试"""

    @pytest.mark.asyncio
    async def test_scheduler_enqueue_dequeue(self):
        """测试入队出队"""
        scheduler = Scheduler(max_queue_size=10)
        await scheduler.start()

        try:
            success = await scheduler.enqueue("run-001", {"task": "test"}, priority=5)
            assert success is True
            assert scheduler.size == 1

            item = await scheduler.dequeue(timeout=1.0)
            assert item is not None
            run_id, data = item
            assert run_id == "run-001"
            assert data["task"] == "test"
            assert scheduler.size == 0
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_scheduler_priority_order(self):
        """测试优先级排序"""
        scheduler = Scheduler(max_queue_size=10)
        await scheduler.start()

        try:
            await scheduler.enqueue("run-low", {"p": "low"}, priority=1)
            await scheduler.enqueue("run-high", {"p": "high"}, priority=10)
            await scheduler.enqueue("run-mid", {"p": "mid"}, priority=5)

            item1 = await scheduler.dequeue(timeout=1.0)
            assert item1[0] == "run-high"

            item2 = await scheduler.dequeue(timeout=1.0)
            assert item2[0] == "run-mid"

            item3 = await scheduler.dequeue(timeout=1.0)
            assert item3[0] == "run-low"
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_scheduler_is_full(self):
        """测试队列满"""
        scheduler = Scheduler(max_queue_size=2)
        await scheduler.start()

        try:
            await scheduler.enqueue("run-1", {}, priority=1)
            await scheduler.enqueue("run-2", {}, priority=1)

            assert scheduler.is_full is True

            # 队列满时入队应该失败
            success = await scheduler.enqueue("run-3", {}, priority=1, timeout=0.1)
            assert success is False
        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_scheduler_remove(self):
        """测试移除任务"""
        scheduler = Scheduler(max_queue_size=10)
        await scheduler.start()

        try:
            await scheduler.enqueue("run-001", {}, priority=5)
            await scheduler.enqueue("run-002", {}, priority=5)

            assert scheduler.size == 2

            removed = await scheduler.remove("run-001")
            assert removed is True
            assert scheduler.size == 1

            # 移除不存在的任务
            removed = await scheduler.remove("run-999")
            assert removed is False
        finally:
            await scheduler.stop()


class TestStateManager:
    """状态管理器测试"""

    @pytest.mark.asyncio
    async def test_state_manager_add_get(self):
        """测试添加和获取"""
        manager = StateManager()

        await manager.add("run-001", "task-001")
        info = await manager.get("run-001")

        assert info is not None
        assert info.run_id == "run-001"
        assert info.task_id == "task-001"
        assert info.state == RunState.QUEUED

    @pytest.mark.asyncio
    async def test_state_manager_transition(self):
        """测试状态转换"""
        manager = StateManager()

        await manager.add("run-001", "task-001")

        await manager.transition("run-001", RunState.PREPARING)
        info = await manager.get("run-001")
        assert info.state == RunState.PREPARING

        await manager.transition("run-001", RunState.RUNNING)
        info = await manager.get("run-001")
        assert info.state == RunState.RUNNING

        await manager.transition("run-001", RunState.COMPLETED)
        info = await manager.get("run-001")
        assert info.state == RunState.COMPLETED

    @pytest.mark.asyncio
    async def test_state_manager_remove(self):
        """测试移除"""
        manager = StateManager()

        await manager.add("run-001", "task-001")
        await manager.remove("run-001")

        info = await manager.get("run-001")
        assert info is None

    @pytest.mark.asyncio
    async def test_state_manager_count_active(self):
        """测试活跃任务计数"""
        manager = StateManager()

        await manager.add("run-001", "task-001")
        await manager.add("run-002", "task-002")
        await manager.add("run-003", "task-003")

        await manager.transition("run-001", RunState.PREPARING)
        await manager.transition("run-001", RunState.RUNNING)
        await manager.transition("run-002", RunState.PREPARING)
        await manager.transition("run-002", RunState.RUNNING)
        await manager.transition("run-002", RunState.COMPLETED)

        count = await manager.count_active()
        assert count == 2  # QUEUED + RUNNING


class TestEngine:
    """引擎测试"""

    @pytest.fixture
    def mock_transport(self):
        """模拟传输层"""
        transport = MagicMock()
        transport.poll_task = AsyncMock(return_value=None)
        transport.poll_control = AsyncMock(return_value=None)
        transport.report_result = AsyncMock(return_value=True)
        transport.ack_task = AsyncMock(return_value=True)
        transport.ack_control = AsyncMock(return_value=True)
        # P1-DR-04: 运行时控制 deadline 判定改用 transport 权威时钟。
        transport.authoritative_now_ms = AsyncMock(return_value=1_000)
        transport._lease_id = "lease-test"
        transport._worker_id = "worker-test"
        return transport

    @pytest.fixture
    def mock_executor(self):
        """模拟执行器"""
        executor = MagicMock()
        executor.run = AsyncMock()
        executor.cancel = AsyncMock()
        return executor

    @pytest.mark.asyncio
    async def test_engine_start_stop(self, mock_transport, mock_executor):
        """测试引擎启动和停止"""
        engine = Engine(
            transport=mock_transport,
            executor=mock_executor,
            max_concurrent=2,
        )

        await engine.start()

        stats = engine.get_stats()
        assert stats["running"] is True
        assert stats["polling"] is True
        assert stats["max_concurrent"] == 2

        await engine.stop(grace_period=1.0)

        stats = engine.get_stats()
        assert stats["running"] is False

    @pytest.mark.asyncio
    async def test_engine_cancel_queued_task(self, mock_transport, mock_executor):
        """测试取消队列中的任务"""
        engine = Engine(
            transport=mock_transport,
            executor=mock_executor,
            max_concurrent=2,
        )

        # 手动添加任务到状态管理器
        await engine.state_manager.add("run-001", "task-001")
        await engine.scheduler.enqueue("run-001", object())

        # 单元测试不连接外部 Redis；归属键释放行为由专项测试覆盖。
        with patch.object(engine, "_release_run_ownership", AsyncMock()):
            result = await engine.cancel("run-001", reason="test cancel")
        assert result is True

        # 验证状态
        info = await engine.state_manager.get("run-001")
        # 任务应该被移除或标记为取消
        assert info is None or info.state == RunState.CANCELLED

    @pytest.mark.asyncio
    async def test_engine_get_stats(self, mock_transport, mock_executor):
        """测试获取统计信息"""
        engine = Engine(
            transport=mock_transport,
            executor=mock_executor,
            max_concurrent=3,
        )

        stats = engine.get_stats()

        assert "running" in stats
        assert "polling" in stats
        assert "queue_size" in stats
        assert "max_concurrent" in stats
        assert stats["max_concurrent"] == 3

    def test_build_payload_requires_source_bundle(self, mock_transport, mock_executor):
        engine = Engine(transport=mock_transport, executor=mock_executor)
        task_msg = MagicMock()
        task_msg.project_type = "code"
        task_msg.params = {}
        task_msg.environment = {}
        task_msg.source_bundle = None

        with pytest.raises(ValueError, match="source_bundle"):
            engine._build_payload(task_msg)

    @pytest.mark.asyncio
    async def test_execute_task_requires_project_fetcher_for_source_bundle(
        self,
        mock_transport,
        mock_executor,
    ):
        engine = Engine(transport=mock_transport, executor=mock_executor)
        bundle = SourceBundle(
            uri="pgartifact://" + "a" * 64,
            sha256="a" * 64,
            size=10,
        )
        task_msg = MagicMock()
        task_msg.task_id = "task-1"
        task_msg.project_id = "proj-1"
        task_msg.project_type = "code"
        task_msg.params = {}
        task_msg.environment = {}
        task_msg.timeout = 60
        task_msg.priority = 0
        task_msg.source_bundle = bundle
        task_msg.entry_point = "main.py"

        context = MagicMock()
        context.run_id = "run-1"
        context.project_id = "proj-1"
        context.labels = {}
        await engine.state_manager.add("run-1", "task-1")

        result = await engine._execute_task(context, task_msg)

        assert result.status.value == "failed"
        assert "project_fetcher" in result.error_message

    def test_builtin_plan_requires_workspace_path(self, mock_transport, mock_executor):
        engine = Engine(transport=mock_transport, executor=mock_executor)
        context = MagicMock()
        context.timeout_seconds = 60
        context.memory_limit_mb = 0
        context.cpu_limit_seconds = 0
        payload = TaskPayload(entry_point="main.py", workspace_path=None)
        runtime_handle = MagicMock()
        runtime_handle.python_executable = "python"

        with pytest.raises(ValueError, match="workspace_path"):
            engine._build_fallback_plan(context, payload, runtime_handle)

    @pytest.mark.asyncio
    async def test_prepare_runtime_requires_runtime_manager_and_spec(self, mock_transport, mock_executor):
        engine = Engine(transport=mock_transport, executor=mock_executor)
        context = RunContext(
            run_id="run-1",
            task_id="task-1",
            project_id="proj-1",
        )

        with pytest.raises(RuntimeError, match="runtime_spec"):
            await engine._prepare_runtime(context)

    @pytest.mark.asyncio
    async def test_report_result_failure_does_not_ack_task(self, mock_transport, mock_executor):
        mock_transport.report_result = AsyncMock(return_value=False)
        engine = Engine(transport=mock_transport, executor=mock_executor)
        # 实例属性覆盖类常量，把 _settle_with_retry 的指数退避收敛到 0，
        # 避免失败路径在单测里真实 sleep 1+2+4+8 秒（重试次数语义不变）。
        engine._SETTLE_BACKOFF_BASE_SECONDS = 0.0
        context = RunContext(
            run_id="run-1",
            task_id="task-1",
            project_id="proj-1",
            receipt="receipt-1",
        )
        result = ExecResult(run_id="run-1", status=RunStatus.SUCCESS)
        await engine.state_manager.add("run-1", "task-1", receipt="receipt-1")

        with pytest.raises(RuntimeError, match="结果上报失败"):
            await engine._report_result(context, result)

        mock_transport.ack_task.assert_not_awaited()
        assert await engine.state_manager.get("run-1") is not None

    @pytest.mark.asyncio
    async def test_report_result_ack_failure_keeps_task_state(self, mock_transport, mock_executor):
        mock_transport.ack_task = AsyncMock(return_value=False)
        engine = Engine(transport=mock_transport, executor=mock_executor)
        # 同上：注入零退避，消除失败路径的真实指数退避 sleep。
        engine._SETTLE_BACKOFF_BASE_SECONDS = 0.0
        context = RunContext(
            run_id="run-1",
            task_id="task-1",
            project_id="proj-1",
            receipt="receipt-1",
        )
        result = ExecResult(run_id="run-1", status=RunStatus.SUCCESS)
        await engine.state_manager.add("run-1", "task-1", receipt="receipt-1")

        with pytest.raises(RuntimeError, match="任务 ACK 失败"):
            await engine._report_result(context, result)

        assert await engine.state_manager.get("run-1") is not None

    @pytest.mark.asyncio
    async def test_runtime_control_result_failure_does_not_ack(
        self,
        mock_transport,
        mock_executor,
        monkeypatch,
    ):
        mock_transport.send_control_result = AsyncMock(return_value=False)
        engine = Engine(transport=mock_transport, executor=mock_executor)
        monkeypatch.setattr(
            "antcode_worker.runtime.uv_manager.uv_manager.get_platform_info_async",
            AsyncMock(return_value={"platform": "test"}),
        )
        control = MagicMock()
        control.payload = {
            "action": "get_platform_info",
            "request_id": "req-1",
            "expires_at_ms": FUTURE_RUNTIME_DEADLINE_MS,
            "reply_stream": "reply-stream",
            "payload": {},
        }
        control.receipt = "receipt-1"

        with pytest.raises(RuntimeError, match="控制结果发送失败"):
            await engine._handle_runtime_control(control)

        mock_transport.ack_control.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runtime_control_result_owns_event_ack(
        self,
        mock_transport,
        mock_executor,
        monkeypatch,
    ):
        mock_transport.send_control_result = AsyncMock(return_value=True)
        engine = Engine(transport=mock_transport, executor=mock_executor)
        monkeypatch.setattr(
            "antcode_worker.runtime.uv_manager.uv_manager.get_platform_info_async",
            AsyncMock(return_value={"platform": "test"}),
        )
        control = MagicMock()
        control.payload = {
            "action": "get_platform_info",
            "request_id": "req-1",
            "expires_at_ms": FUTURE_RUNTIME_DEADLINE_MS,
            "reply_stream": "reply-stream",
            "payload": {},
        }
        control.receipt = "receipt-1"

        await engine._handle_runtime_control(control)

        assert mock_transport.send_control_result.await_args.kwargs["receipt"] == "receipt-1"
        mock_transport.ack_control.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runtime_control_missing_request_id_is_acked_and_dropped(
        self,
        mock_transport,
        mock_executor,
        monkeypatch,
    ):
        """W4 回归：有 receipt 但缺 request_id 的畸形事件必须就地 ACK。

        退化行为是直接 raise（settlement try 块之前），事件永不 ACK →
        Direct 的 PendingControlRecovery / Gateway 的 WatchControl PEL
        重放会无限重投同一条坏消息。
        """
        mock_transport.send_control_result = AsyncMock(return_value=True)
        action = AsyncMock(return_value={"platform": "must-not-run"})
        monkeypatch.setattr(
            "antcode_worker.runtime.uv_manager.uv_manager.get_platform_info_async",
            action,
        )
        engine = Engine(transport=mock_transport, executor=mock_executor)
        control = MagicMock(
            payload={
                "action": "get_platform_info",
                "expires_at_ms": FUTURE_RUNTIME_DEADLINE_MS,
            },
            receipt="receipt-1",
        )

        await engine._handle_runtime_control(control)

        mock_transport.ack_control.assert_awaited_once_with("receipt-1")
        mock_transport.send_control_result.assert_not_awaited()
        action.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runtime_control_missing_request_id_ack_failure_is_exposed(
        self,
        mock_transport,
        mock_executor,
    ):
        """畸形事件 ACK 失败必须暴露，事件保留到下一次重投。"""
        mock_transport.send_control_result = AsyncMock(return_value=True)
        mock_transport.ack_control = AsyncMock(side_effect=RuntimeError("ack down"))
        engine = Engine(transport=mock_transport, executor=mock_executor)
        control = MagicMock(payload={"action": "noop"}, receipt="receipt-1")

        with pytest.raises(RuntimeError, match="ack down"):
            await engine._handle_runtime_control(control)

        mock_transport.ack_control.assert_awaited_once_with("receipt-1")
        mock_transport.send_control_result.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runtime_control_without_receipt_still_raises(
        self,
        mock_transport,
        mock_executor,
    ):
        """无 receipt 的事件没有 settlement 通路，保持 fail-fast。"""
        mock_transport.send_control_result = AsyncMock(return_value=True)
        engine = Engine(transport=mock_transport, executor=mock_executor)
        control = MagicMock(payload={"action": "noop", "request_id": "req-1"}, receipt="")

        with pytest.raises(RuntimeError, match="缺少 receipt"):
            await engine._handle_runtime_control(control)

        mock_transport.ack_control.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runtime_control_duplicate_receipt_is_not_scheduled_twice(
        self,
        mock_transport,
        mock_executor,
    ):
        engine = Engine(transport=mock_transport, executor=mock_executor)
        release = asyncio.Event()

        async def wait_for_release(_control):
            await release.wait()

        engine._handle_runtime_control = wait_for_release
        control = MagicMock(receipt="receipt-1")

        assert engine._schedule_runtime_control(control) is True
        assert engine._schedule_runtime_control(control) is False
        release.set()
        await asyncio.gather(*engine._inflight_controls)
        await asyncio.sleep(0)

        assert "receipt-1" not in engine._runtime_control_tasks
        assert not engine._runtime_control_receipts
        assert not engine._inflight_controls

    @pytest.mark.asyncio
    async def test_cancelled_runtime_action_stays_unsettled_and_can_be_redelivered(
        self,
        mock_transport,
        mock_executor,
        monkeypatch,
    ):
        mock_transport.send_control_result = AsyncMock(return_value=True)
        engine = Engine(transport=mock_transport, executor=mock_executor)
        action_started = asyncio.Event()
        release = asyncio.Event()

        async def cancellable_action():
            action_started.set()
            await release.wait()
            return {"platform": "test"}

        monkeypatch.setattr(
            "antcode_worker.runtime.uv_manager.uv_manager.get_platform_info_async",
            cancellable_action,
        )
        control = MagicMock(
            payload={
                "action": "get_platform_info",
                "request_id": "req-1",
                "expires_at_ms": FUTURE_RUNTIME_DEADLINE_MS,
            },
            receipt="receipt-1",
        )

        assert engine._schedule_runtime_control(control) is True
        task = engine._runtime_control_tasks["receipt-1"]
        await action_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        mock_transport.send_control_result.assert_not_awaited()
        mock_transport.ack_control.assert_not_awaited()
        assert "receipt-1" not in engine._runtime_control_tasks
        assert task not in engine._runtime_control_receipts

        release.set()
        assert engine._schedule_runtime_control(control) is True
        await asyncio.gather(*engine._inflight_controls)

    @pytest.mark.asyncio
    async def test_runtime_control_background_failure_is_observed(
        self,
        mock_transport,
        *,
        mock_executor,
        monkeypatch,
    ):
        engine = Engine(transport=mock_transport, executor=mock_executor)
        observed_logger = MagicMock()
        monkeypatch.setattr("antcode_worker.engine.engine.logger", observed_logger)

        async def fail_control():
            raise RuntimeError("control delivery failed")

        engine._handle_runtime_control = lambda _control: fail_control()
        control = MagicMock(receipt="receipt-1")
        assert engine._schedule_runtime_control(control) is True
        task = engine._runtime_control_tasks["receipt-1"]
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert task not in engine._inflight_controls
        assert "receipt-1" not in engine._runtime_control_tasks
        assert task not in engine._runtime_control_receipts
        observed_logger.opt.assert_called_once()
        observed_logger.opt.return_value.error.assert_called_once_with("运行时控制后台任务失败")
