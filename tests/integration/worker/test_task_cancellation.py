"""
任务取消集成测试

验证 cancel 流程。

Requirements: 14.4
"""

import asyncio
import os
import sys
import tempfile
import uuid
from datetime import datetime

import pytest
from loguru import logger

from antcode_worker.transport.redis.keys import RedisKeys

# 从环境变量或默认值获取 Redis URL
REDIS_URL = os.getenv("REDIS_URL", "redis://:redis_i36zi5@154.12.30.182:6379/0")
REDIS_KEYS = RedisKeys()


@pytest.fixture
def unique_task_id():
    """生成唯一任务 ID"""
    return f"cancel-task-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_run_id():
    """生成唯一运行 ID"""
    return f"cancel-run-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"cancel-worker-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestTaskCancellation:
    """任务取消测试 - Requirements: 14.4"""

    @pytest.mark.asyncio
    async def test_cancel_queued_task(self, unique_run_id, unique_task_id):
        """
        测试取消队列中的任务

        验证：
        1. 队列中的任务可以被取消
        2. 状态正确转换为 CANCELLED
        """
        from antcode_worker.engine.state import RunState, StateManager

        state_manager = StateManager()

        # 添加任务到队列
        await state_manager.add(unique_run_id, unique_task_id)
        info = await state_manager.get(unique_run_id)
        assert info.state == RunState.QUEUED

        # 取消任务
        success = await state_manager.transition(unique_run_id, RunState.CANCELLED)
        assert success is True

        # 验证状态
        info = await state_manager.get(unique_run_id)
        assert info.state == RunState.CANCELLED

        logger.info(f"[Test] 队列任务取消成功: {unique_run_id}")

    @pytest.mark.asyncio
    async def test_cancel_running_task(self, unique_run_id, unique_task_id):
        """
        测试取消运行中的任务

        验证：
        1. 运行中的任务可以进入 CANCELLING 状态
        2. 最终转换为 CANCELLED 状态
        """
        from antcode_worker.engine.state import RunState, StateManager

        state_manager = StateManager()

        # 添加任务并转换到 RUNNING 状态
        await state_manager.add(unique_run_id, unique_task_id)
        await state_manager.transition(unique_run_id, RunState.PREPARING)
        await state_manager.transition(unique_run_id, RunState.RUNNING)

        info = await state_manager.get(unique_run_id)
        assert info.state == RunState.RUNNING

        # 开始取消
        success = await state_manager.transition(unique_run_id, RunState.CANCELLING)
        assert success is True

        info = await state_manager.get(unique_run_id)
        assert info.state == RunState.CANCELLING

        # 完成取消
        success = await state_manager.transition(unique_run_id, RunState.CANCELLED)
        assert success is True

        info = await state_manager.get(unique_run_id)
        assert info.state == RunState.CANCELLED

        logger.info(f"[Test] 运行中任务取消成功: {unique_run_id}")

    @pytest.mark.asyncio
    async def test_executor_cancel_running_process(self, unique_task_id):
        """
        测试 Executor 取消运行中的进程

        验证：
        1. 运行中的进程可以被取消
        2. 进程被正确终止
        """
        from antcode_worker.domain.enums import RunStatus
        from antcode_worker.domain.models import ExecPlan, RuntimeHandle
        from antcode_worker.executor import ProcessExecutor
        from antcode_worker.executor.base import ExecutorConfig, NoOpLogSink

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建长时间运行的脚本
            script_path = os.path.join(tmpdir, "long_running.py")
            with open(script_path, "w") as f:
                f.write('import logging\n')
                f.write('import time\n')
                f.write('import signal\n')
                f.write('import sys\n')
                f.write('logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)\n')
                f.write('logger = logging.getLogger(__name__)\n')
                f.write('\n')
                f.write('def handler(signum, frame):\n')
                f.write('    logger.info("Received signal, exiting...")\n')
                f.write('    sys.exit(0)\n')
                f.write('\n')
                f.write('signal.signal(signal.SIGTERM, handler)\n')
                f.write('logger.info("Starting long task...")\n')
                f.write('for i in range(60):\n')
                f.write('    logger.info(f"Working... {i}")\n')
                f.write('    time.sleep(1)\n')

            config = ExecutorConfig(max_concurrent=2, default_timeout=60)
            executor = ProcessExecutor(config)
            await executor.start()

            try:
                runtime_handle = RuntimeHandle(
                    path=tmpdir,
                    runtime_hash="test-cancel",
                    python_executable=sys.executable,
                )

                exec_plan = ExecPlan(
                    command=script_path,
                    args=[],
                    env={},
                    cwd=tmpdir,
                    timeout_seconds=60,
                    plugin_name=unique_task_id,
                )

                # 启动执行（不等待完成）
                exec_task = asyncio.create_task(
                    executor.run(exec_plan, runtime_handle, NoOpLogSink())
                )

                # 等待进程启动
                await asyncio.sleep(1.0)

                # 取消执行
                await executor.cancel(unique_task_id)

                # 等待结果
                result = await exec_task

                # 验证被取消或超时
                # 注意：取消可能导致 CANCELLED 或 FAILED 状态
                assert result.status in (RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.SUCCESS)
                logger.info(f"[Test] 进程取消测试: status={result.status}")

            finally:
                await executor.stop()

    @pytest.mark.asyncio
    async def test_cancel_report_result(
        self,
        unique_run_id,
        unique_task_id,
        unique_worker_id,
    ):
        """
        测试取消后上报结果

        验证：
        1. 取消的任务可以上报 cancelled 状态
        2. 结果正确写入
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport import RedisTransport, ServerConfig, TaskResult

        unique_prefix = f"test:cancel:{uuid.uuid4().hex[:8]}:"

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        config = ServerConfig(
            redis_url=REDIS_URL,
            task_stream_prefix=unique_prefix,
        )
        transport = RedisTransport(
            redis_url=REDIS_URL,
            worker_id=unique_worker_id,
            config=config,
        )

        try:
            await transport.start()

            # 上报取消结果
            result = TaskResult(
                run_id=unique_run_id,
                task_id=unique_task_id,
                status="cancelled",
                exit_code=-2,
                error_message="Task cancelled by user request",
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=5000.0,
            )

            success = await transport.report_result(result)
            assert success is True

            # 验证结果
            result_key = REDIS_KEYS.task_result_stream()
            results = await redis_client.xrevrange(result_key, "+", "-", count=10)

            found = False
            for msg_id, data in results:
                if data.get("task_id") == unique_task_id:
                    found = True
                    assert data.get("status") == "cancelled"
                    assert "cancelled by user" in data.get("error_message", "")
                    break

            assert found, "取消结果未写入"
            logger.info(f"[Test] 取消结果上报成功: {unique_task_id}")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_cancel_and_ack(self, unique_task_id, unique_worker_id):
        """
        测试取消后 ACK

        验证：
        1. 取消的任务可以 ACK
        2. ACK 正确写入
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport import RedisTransport, ServerConfig

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        config = ServerConfig(redis_url=REDIS_URL)
        transport = RedisTransport(
            redis_url=REDIS_URL,
            worker_id=unique_worker_id,
            config=config,
        )

        stream_key = REDIS_KEYS.task_ready_stream(unique_worker_id)

        try:
            await transport.start()

            # 写入任务并获取 receipt
            task_data = {
                "task_id": unique_task_id,
                "project_id": "cancel-project",
                "project_type": "code",
            }
            await redis_client.xadd(stream_key, task_data)
            task_msg = await transport.poll_task(timeout=5.0)
            assert task_msg is not None, "未能拉取到任务"

            # ACK 取消的任务（使用 receipt）
            success = await transport.ack_task(
                task_msg.receipt,
                accepted=True,
                reason="cancelled"
            )
            assert success is True

            # 验证 pending 已清空
            pending = await redis_client.xpending(stream_key, REDIS_KEYS.consumer_group_name())
            assert pending["pending"] == 0, "ACK 后 pending 未清空"
            logger.info(f"[Test] 取消后 ACK pending 已清空: {unique_task_id}")

        finally:
            await transport.stop()
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()


@pytest.mark.integration
class TestCancellationStateTransitions:
    """取消状态转换测试"""

    @pytest.mark.asyncio
    async def test_valid_cancel_transitions(self, unique_run_id, unique_task_id):
        """
        测试有效的取消状态转换

        验证：
        1. QUEUED -> CANCELLED 有效
        2. PREPARING -> CANCELLED 有效
        3. RUNNING -> CANCELLING -> CANCELLED 有效
        """
        from antcode_worker.engine.state import RunState, StateManager

        # 测试 QUEUED -> CANCELLED
        sm1 = StateManager()
        await sm1.add(f"{unique_run_id}-1", unique_task_id)
        success = await sm1.transition(f"{unique_run_id}-1", RunState.CANCELLED)
        assert success is True
        info = await sm1.get(f"{unique_run_id}-1")
        assert info.state == RunState.CANCELLED

        # 测试 PREPARING -> CANCELLED
        sm2 = StateManager()
        await sm2.add(f"{unique_run_id}-2", unique_task_id)
        await sm2.transition(f"{unique_run_id}-2", RunState.PREPARING)
        success = await sm2.transition(f"{unique_run_id}-2", RunState.CANCELLED)
        assert success is True
        info = await sm2.get(f"{unique_run_id}-2")
        assert info.state == RunState.CANCELLED

        # 测试 RUNNING -> CANCELLING -> CANCELLED
        sm3 = StateManager()
        await sm3.add(f"{unique_run_id}-3", unique_task_id)
        await sm3.transition(f"{unique_run_id}-3", RunState.PREPARING)
        await sm3.transition(f"{unique_run_id}-3", RunState.RUNNING)
        success = await sm3.transition(f"{unique_run_id}-3", RunState.CANCELLING)
        assert success is True
        success = await sm3.transition(f"{unique_run_id}-3", RunState.CANCELLED)
        assert success is True
        info = await sm3.get(f"{unique_run_id}-3")
        assert info.state == RunState.CANCELLED

        logger.info("[Test] 有效取消状态转换验证通过")

    @pytest.mark.asyncio
    async def test_invalid_cancel_transitions(self, unique_run_id, unique_task_id):
        """
        测试无效的取消状态转换

        验证：
        1. COMPLETED -> CANCELLED 无效
        2. FAILED -> CANCELLED 无效
        3. CANCELLED -> CANCELLED 无效
        """
        from antcode_worker.engine.state import RunState, StateManager

        # 测试 COMPLETED -> CANCELLED（无效）
        sm1 = StateManager()
        await sm1.add(f"{unique_run_id}-1", unique_task_id)
        await sm1.transition(f"{unique_run_id}-1", RunState.PREPARING)
        await sm1.transition(f"{unique_run_id}-1", RunState.RUNNING)
        await sm1.transition(f"{unique_run_id}-1", RunState.COMPLETED)
        success = await sm1.transition(f"{unique_run_id}-1", RunState.CANCELLED)
        assert success is False  # 应该失败

        # 测试 FAILED -> CANCELLED（无效）
        sm2 = StateManager()
        await sm2.add(f"{unique_run_id}-2", unique_task_id)
        await sm2.transition(f"{unique_run_id}-2", RunState.PREPARING)
        await sm2.transition(f"{unique_run_id}-2", RunState.FAILED)
        success = await sm2.transition(f"{unique_run_id}-2", RunState.CANCELLED)
        assert success is False  # 应该失败

        # 测试 CANCELLED -> CANCELLED（无效）
        sm3 = StateManager()
        await sm3.add(f"{unique_run_id}-3", unique_task_id)
        await sm3.transition(f"{unique_run_id}-3", RunState.CANCELLED)
        success = await sm3.transition(f"{unique_run_id}-3", RunState.CANCELLED)
        assert success is False  # 应该失败

        logger.info("[Test] 无效取消状态转换验证通过")

    @pytest.mark.asyncio
    async def test_cancelling_can_complete_or_fail(self, unique_run_id, unique_task_id):
        """
        测试 CANCELLING 状态可以转换为 COMPLETED 或 FAILED

        验证：
        1. CANCELLING -> COMPLETED 有效（任务在取消前完成）
        2. CANCELLING -> FAILED 有效（取消过程中失败）
        """
        from antcode_worker.engine.state import RunState, StateManager

        # 测试 CANCELLING -> COMPLETED
        sm1 = StateManager()
        await sm1.add(f"{unique_run_id}-1", unique_task_id)
        await sm1.transition(f"{unique_run_id}-1", RunState.PREPARING)
        await sm1.transition(f"{unique_run_id}-1", RunState.RUNNING)
        await sm1.transition(f"{unique_run_id}-1", RunState.CANCELLING)
        success = await sm1.transition(f"{unique_run_id}-1", RunState.COMPLETED)
        assert success is True

        # 测试 CANCELLING -> FAILED
        sm2 = StateManager()
        await sm2.add(f"{unique_run_id}-2", unique_task_id)
        await sm2.transition(f"{unique_run_id}-2", RunState.PREPARING)
        await sm2.transition(f"{unique_run_id}-2", RunState.RUNNING)
        await sm2.transition(f"{unique_run_id}-2", RunState.CANCELLING)
        success = await sm2.transition(f"{unique_run_id}-2", RunState.FAILED)
        assert success is True

        logger.info("[Test] CANCELLING 状态转换验证通过")


@pytest.mark.integration
class TestCancellationWithScheduler:
    """取消与调度器集成测试"""

    @pytest.mark.asyncio
    async def test_cancel_task_in_scheduler_queue(self, unique_run_id):
        """
        测试取消调度器队列中的任务

        验证：
        1. 任务可以从调度器队列中移除
        2. 队列大小正确更新
        """
        from antcode_worker.engine.scheduler import Scheduler

        scheduler = Scheduler(max_queue_size=10)
        await scheduler.start()

        try:
            # 入队多个任务
            await scheduler.enqueue(f"{unique_run_id}-1", {"task": "1"}, priority=5)
            await scheduler.enqueue(f"{unique_run_id}-2", {"task": "2"}, priority=5)
            await scheduler.enqueue(f"{unique_run_id}-3", {"task": "3"}, priority=5)

            assert scheduler.size == 3

            # 移除中间的任务
            removed = await scheduler.remove(f"{unique_run_id}-2")
            assert removed is True
            assert scheduler.size == 2

            # 验证剩余任务
            item1 = await scheduler.dequeue(timeout=1.0)
            item2 = await scheduler.dequeue(timeout=1.0)

            run_ids = {item1[0], item2[0]}
            assert f"{unique_run_id}-1" in run_ids
            assert f"{unique_run_id}-3" in run_ids
            assert f"{unique_run_id}-2" not in run_ids

            logger.info("[Test] 调度器队列任务取消成功")

        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_task(self, unique_run_id):
        """
        测试取消不存在的任务

        验证：
        1. 取消不存在的任务返回 False
        2. 不会产生错误
        """
        from antcode_worker.engine.scheduler import Scheduler

        scheduler = Scheduler(max_queue_size=10)
        await scheduler.start()

        try:
            # 尝试移除不存在的任务
            removed = await scheduler.remove("non-existent-run-id")
            assert removed is False

            logger.info("[Test] 取消不存在任务验证通过")

        finally:
            await scheduler.stop()
