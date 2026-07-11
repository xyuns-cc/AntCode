"""
Worker Engine 单元测试

测试任务引擎核心功能：
- 引擎启动/停止
- 任务调度
- 状态管理
- 取消任务
"""

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
            "reply_stream": "reply-stream",
            "payload": {},
        }
        control.receipt = "receipt-1"

        with pytest.raises(RuntimeError, match="控制结果发送失败"):
            await engine._handle_runtime_control(control)

        mock_transport.ack_control.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_runtime_control_ack_failure_is_exposed(
        self,
        mock_transport,
        mock_executor,
        monkeypatch,
    ):
        mock_transport.send_control_result = AsyncMock(return_value=True)
        mock_transport.ack_control = AsyncMock(return_value=False)
        engine = Engine(transport=mock_transport, executor=mock_executor)
        monkeypatch.setattr(
            "antcode_worker.runtime.uv_manager.uv_manager.get_platform_info_async",
            AsyncMock(return_value={"platform": "test"}),
        )
        control = MagicMock()
        control.payload = {
            "action": "get_platform_info",
            "request_id": "req-1",
            "reply_stream": "reply-stream",
            "payload": {},
        }
        control.receipt = "receipt-1"

        with pytest.raises(RuntimeError, match="控制消息 ACK 失败"):
            await engine._handle_runtime_control(control)
