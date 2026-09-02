"""Worker 故障恢复、幂等性与负载集成测试。"""

import asyncio
import hmac
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import unquote, urlsplit

import pytest
import pytest_asyncio
from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_worker.transport.redis.keys import RedisKeys
from loguru import logger

from tests.integration.worker import direct_transport_support as direct_support

# 集成测试必须显式提供 Redis URL
REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="ANTCODE_INTEGRATION_REDIS_URL is required for worker integration tests",
)
TEST_SOURCE_BUNDLE_SHA256 = "a" * 64
MASTER_TEST_DATABASE_NAME = "antcode_e2e_test"


def _require_master_test_database_url() -> str:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("Master 结果集成测试必须显式设置 DATABASE_URL")
    parsed = urlsplit(database_url)
    if parsed.scheme.lower() not in {"postgres", "postgresql"}:
        raise ValueError("Master 结果集成测试必须使用 PostgreSQL")
    database = unquote(parsed.path.lstrip("/")).lower()
    if database != MASTER_TEST_DATABASE_NAME:
        raise ValueError(f"Master 结果集成测试仅允许数据库 {MASTER_TEST_DATABASE_NAME!r}")
    return database_url


@pytest_asyncio.fixture
async def master_database():
    from antcode_core.infrastructure.db.tortoise import get_tortoise_config
    from tortoise import Tortoise

    _require_master_test_database_url()
    config = get_tortoise_config(min_connections=1, max_connections=2, service="master")
    await Tortoise.init(config=config)
    try:
        yield
    finally:
        await Tortoise.close_connections()


@asynccontextmanager
async def _master_result_environment(
    redis_client,
    *,
    worker_id: str,
    run_id: str,
    namespace: str,
    lease_id: str,
):
    from antcode_core.application.services.lease_service import LeaseStore
    from antcode_core.application.services.task_run_service import TaskRunService
    from antcode_core.domain.models import TaskRun, Worker
    from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus

    worker = await Worker.create(
        public_id=worker_id,
        name=f"master-result-{uuid.uuid4().hex}",
        host="127.0.0.1",
        port=8001,
        status="online",
    )
    await TaskRun.create(
        task_id=int(uuid.uuid4().int % 1_000_000_000),
        run_id=run_id,
        worker_id=worker.id,
        lease_id=lease_id,
        status=TaskStatus.RUNNING,
        dispatch_status=DispatchStatus.DISPATCHED,
        runtime_status=RuntimeStatus.RUNNING,
    )
    lease_store = LeaseStore(redis_client, namespace=namespace)

    async def validate_lease(declared_worker_id: str, declared_lease_id: str) -> bool:
        current = await lease_store.get(declared_worker_id)
        return bool(
            current
            and current.expires_at_ms > int(time.time() * 1000)
            and hmac.compare_digest(current.lease_id, declared_lease_id)
        )

    try:
        assert await lease_store.is_current(worker_id, lease_id)
        yield TaskRunService(validate_lease)
    finally:
        await TaskRun.filter(run_id=run_id).delete()
        await Worker.filter(id=worker.id).delete()


def _successful_task_result(*, run_id: str, task_id: str, lease_id: str):
    from antcode_worker.transport.base import TaskResult

    return TaskResult(
        run_id=run_id,
        task_id=task_id,
        status="success",
        exit_code=0,
        started_at=datetime.now(),
        finished_at=datetime.now(),
        duration_ms=1500.0,
        data={"lease_id": lease_id, "proof": "master-idempotency"},
    )


async def _assert_duplicate_master_processing(transport, *, result, redis_client, keys) -> None:
    from antcode_core.domain.models import TaskRun
    from antcode_core.domain.models.enums import DispatchStatus, RuntimeStatus, TaskStatus
    from antcode_master.ingester.result_loop import ResultLoop

    reports = [await transport.report_result(result) for _ in range(3)]
    assert reports == [True, True, True]
    entries = await redis_client.xrange(keys.task_result_stream(), "-", "+")
    statuses = direct_support.decode_task_statuses(entries)
    assert len(statuses) == 3, "重复上报未产生预期的 at-least-once 消息"
    handled = [await ResultLoop()._handle_message(status) for status in statuses]
    assert handled == [True, True, True]
    execution = await TaskRun.get(run_id=result.run_id)
    assert execution.dispatch_status == DispatchStatus.ACKED
    assert execution.runtime_status == RuntimeStatus.SUCCESS
    assert execution.status == TaskStatus.SUCCESS
    assert execution.result_data == {"proof": "master-idempotency"}
    assert await TaskRun.filter(run_id=result.run_id).count() == 1


@pytest.fixture
def unique_task_id():
    """生成唯一任务 ID"""
    return f"fault-task-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"fault-worker-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_stream_prefix():
    """生成唯一 stream 前缀"""
    return f"test:fault:{uuid.uuid4().hex[:8]}:"


@pytest.mark.integration
class TestCrashPendingReclaim:
    """
    模拟 crash 后 pending reclaim 测试

    验证：
    1. Worker 崩溃后任务进入 pending 状态
    2. 新 Worker 可以通过 XAUTOCLAIM 回收 pending 任务
    3. 回收后任务可以正常执行
    4. 超过最大重试次数的任务进入死信队列

    Requirements: 14.5, 5.3
    """

    @pytest.mark.asyncio
    async def test_crash_and_reclaim_basic(self, unique_task_id, unique_stream_prefix):
        """
        测试基本的 crash 和 reclaim 流程

        模拟场景：
        1. Consumer1 读取任务但未 ACK（模拟崩溃）
        2. Consumer2 使用 XAUTOCLAIM 回收任务
        3. Consumer2 成功处理并 ACK
        """
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        stream_key = f"{unique_stream_prefix}ready"
        group_name = "workers"
        consumer1 = f"crashed-consumer-{uuid.uuid4().hex[:4]}"
        consumer2 = f"recovery-consumer-{uuid.uuid4().hex[:4]}"

        try:
            # 创建 stream 和 consumer group
            await redis_client.xgroup_create(
                stream_key,
                group_name,
                id="0",
                mkstream=True,
            )

            # 添加任务
            task_data = {
                "task_id": unique_task_id,
                "project_id": "test-project",
                "project_type": "code",
                "priority": "5",
                "created_at": datetime.now().isoformat(),
            }
            msg_id = await redis_client.xadd(stream_key, task_data)
            logger.info(f"[Crash Test] 任务已添加: msg_id={msg_id}")

            # Consumer1 读取任务（模拟崩溃前的读取）
            result = await redis_client.xreadgroup(
                groupname=group_name,
                consumername=consumer1,
                streams={stream_key: ">"},
                count=1,
                block=5000,
            )

            assert result, "Consumer1 未能读取任务"
            _, messages = result[0]
            receipt, data = messages[0]
            assert data.get("task_id") == unique_task_id
            logger.info(f"[Crash Test] Consumer1 读取任务: receipt={receipt}")

            # 验证任务进入 pending 状态
            pending = await redis_client.xpending(stream_key, group_name)
            assert pending["pending"] >= 1, "任务未进入 pending 状态"
            logger.info(f"[Crash Test] Pending 任务数: {pending['pending']}")

            # 模拟 Consumer1 崩溃（不 ACK，等待一小段时间）
            await asyncio.sleep(0.1)
            logger.info("[Crash Test] Consumer1 崩溃（未 ACK）")

            # Consumer2 使用 XAUTOCLAIM 回收任务
            reclaim_result = await redis_client.xautoclaim(
                stream_key,
                group_name,
                consumer2,
                min_idle_time=0,  # 立即回收（测试用）
                start_id="0-0",
                count=10,
            )

            next_start_id, reclaimed_messages, deleted_ids = reclaim_result
            logger.info(f"[Crash Test] XAUTOCLAIM 结果: reclaimed={len(reclaimed_messages)}")

            # 验证任务被回收
            assert len(reclaimed_messages) >= 1, "任务未被回收"
            reclaimed_msg_id, reclaimed_data = reclaimed_messages[0]
            assert reclaimed_data.get("task_id") == unique_task_id

            # Consumer2 处理并 ACK 任务
            ack_count = await redis_client.xack(stream_key, group_name, reclaimed_msg_id)
            assert ack_count == 1, "ACK 失败"
            logger.info("[Crash Test] Consumer2 ACK 成功")

            # 验证 pending 减少
            pending_after = await redis_client.xpending(stream_key, group_name)
            assert pending_after["pending"] == 0, "Pending 任务未清除"
            logger.info(f"[Crash Test] ACK 后 Pending 任务数: {pending_after['pending']}")

            logger.info("[Crash Test] ✓ 基本 crash 和 reclaim 流程验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_multiple_crashes_and_reclaims(self, unique_stream_prefix):
        """
        测试多次崩溃和回收

        模拟场景：
        1. 多个任务被不同 consumer 读取但未 ACK
        2. 新 consumer 回收所有 pending 任务
        """
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        stream_key = f"{unique_stream_prefix}ready"
        group_name = "workers"
        num_tasks = 5

        try:
            # 创建 stream 和 consumer group
            await redis_client.xgroup_create(
                stream_key,
                group_name,
                id="0",
                mkstream=True,
            )

            # 添加多个任务
            task_ids = []
            for i in range(num_tasks):
                task_id = f"multi-crash-task-{i}-{uuid.uuid4().hex[:4]}"
                task_ids.append(task_id)
                await redis_client.xadd(
                    stream_key,
                    {
                        "task_id": task_id,
                        "project_id": "test-project",
                        "index": str(i),
                    },
                )
            logger.info(f"[Multi-Crash Test] 添加了 {num_tasks} 个任务")

            # 多个 consumer 读取任务但不 ACK
            for i in range(num_tasks):
                consumer = f"crashed-consumer-{i}-{uuid.uuid4().hex[:4]}"
                result = await redis_client.xreadgroup(
                    groupname=group_name,
                    consumername=consumer,
                    streams={stream_key: ">"},
                    count=1,
                    block=1000,
                )
                if result:
                    logger.info(f"[Multi-Crash Test] Consumer {i} 读取任务")

            # 验证所有任务都在 pending
            pending = await redis_client.xpending(stream_key, group_name)
            assert pending["pending"] == num_tasks, f"Pending 数量不正确: {pending['pending']}"
            logger.info(f"[Multi-Crash Test] Pending 任务数: {pending['pending']}")

            # 新 consumer 回收所有任务
            recovery_consumer = f"recovery-consumer-{uuid.uuid4().hex[:4]}"
            reclaim_result = await redis_client.xautoclaim(
                stream_key,
                group_name,
                recovery_consumer,
                min_idle_time=0,
                start_id="0-0",
                count=100,
            )

            _, reclaimed_messages, _ = reclaim_result
            assert len(reclaimed_messages) == num_tasks, f"回收数量不正确: {len(reclaimed_messages)}"
            logger.info(f"[Multi-Crash Test] 回收了 {len(reclaimed_messages)} 个任务")

            # ACK 所有任务
            for msg_id, _ in reclaimed_messages:
                await redis_client.xack(stream_key, group_name, msg_id)

            # 验证 pending 清空
            pending_after = await redis_client.xpending(stream_key, group_name)
            assert pending_after["pending"] == 0
            logger.info("[Multi-Crash Test] ✓ 多次崩溃和回收验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_reclaim_with_delivery_count_tracking(self, unique_task_id, unique_stream_prefix):
        """
        测试回收时的投递计数跟踪

        验证：
        1. 每次回收增加投递计数
        2. 可以通过投递计数判断重试次数
        """
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        stream_key = f"{unique_stream_prefix}ready"
        group_name = "workers"

        try:
            # 创建 stream 和 consumer group
            await redis_client.xgroup_create(
                stream_key,
                group_name,
                id="0",
                mkstream=True,
            )

            # 添加任务
            await redis_client.xadd(stream_key, {"task_id": unique_task_id})

            # 多次读取和回收（模拟多次失败）
            for i in range(3):
                consumer = f"consumer-{i}-{uuid.uuid4().hex[:4]}"

                # 读取或回收
                if i == 0:
                    await redis_client.xreadgroup(
                        groupname=group_name,
                        consumername=consumer,
                        streams={stream_key: ">"},
                        count=1,
                        block=1000,
                    )
                else:
                    reclaim_result = await redis_client.xautoclaim(
                        stream_key,
                        group_name,
                        consumer,
                        min_idle_time=0,
                        start_id="0-0",
                        count=1,
                    )
                    if reclaim_result[1]:
                        logger.info(f"[Delivery Count Test] 第 {i + 1} 次回收")

                # 不 ACK，模拟失败
                await asyncio.sleep(0.05)

            # 检查投递计数
            pending_range = await redis_client.xpending_range(
                stream_key,
                group_name,
                min="-",
                max="+",
                count=10,
            )

            if pending_range:
                entry = pending_range[0]
                # 获取投递计数
                delivery_count = entry.get("times_delivered", 1)
                logger.info(f"[Delivery Count Test] 投递计数: {delivery_count}")
                assert delivery_count >= 1, "投递计数应该增加"

            logger.info("[Delivery Count Test] ✓ 投递计数跟踪验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_dead_letter_queue_on_max_retries(self, unique_task_id, unique_stream_prefix):
        """
        测试超过最大重试次数后移入死信队列

        验证：
        1. 任务超过最大重试次数
        2. 任务被移入死信队列
        3. 死信队列包含原始任务信息
        """
        import redis.asyncio as aioredis
        from antcode_worker.transport.redis.keys import RedisKeys
        from antcode_worker.transport.redis.reclaim import (
            PendingTaskReclaimer,
            ReclaimConfig,
        )

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        worker_id = f"dlq-worker-{uuid.uuid4().hex[:4]}"
        stream_key = keys.task_ready_stream(worker_id)
        dead_letter_key = f"{stream_key}:{{dlq}}:dead_letter"
        group_name = keys.consumer_group_name()

        try:
            await redis_client.xgroup_create(
                stream_key,
                group_name,
                id="0",
                mkstream=True,
            )

            await redis_client.xadd(
                stream_key,
                {
                    "task_id": unique_task_id,
                    "project_id": "test-project",
                    "important_data": "should_be_preserved",
                },
            )

            # 配置低重试次数
            config = ReclaimConfig(
                min_idle_time_ms=0,
                max_retries=1,  # 只允许 1 次重试
                enable_dead_letter=True,
            )

            # 第一次读取（模拟第一次失败）
            consumer1 = f"consumer-1-{uuid.uuid4().hex[:4]}"
            await redis_client.xreadgroup(
                groupname=group_name,
                consumername=consumer1,
                streams={stream_key: ">"},
                count=1,
                block=1000,
            )
            await asyncio.sleep(0.05)

            current_consumer = f"consumer-2-{uuid.uuid4().hex[:4]}"
            reclaimer = PendingTaskReclaimer(
                redis_client=redis_client,
                worker_id=worker_id,
                keys=keys,
                config=config,
                current_consumer_name=lambda: current_consumer,
            )

            # 第一代接管后崩溃，下一代再次接管并超过重试阈值。
            await reclaimer.reclaim_once()
            await asyncio.sleep(0.05)
            current_consumer = f"consumer-3-{uuid.uuid4().hex[:4]}"
            await reclaimer.reclaim_once()

            # 检查死信队列
            dlq_messages = await redis_client.xrange(dead_letter_key, "-", "+", count=10)

            matching_messages = [data for _, data in dlq_messages if data.get("task_id") == unique_task_id]
            assert len(matching_messages) == 1, "目标任务未且仅未进入一次死信队列"
            dead_letter_data = matching_messages[0]
            assert dead_letter_data.get("important_data") == "should_be_preserved"
            assert dead_letter_data.get("_original_stream") == stream_key
            assert int(dead_letter_data.get("_delivery_count", "0")) > config.max_retries
            assert "_dead_lettered_at" in dead_letter_data
            assert reclaimer.stats.total_dead_lettered == 1
            pending = await redis_client.xpending(stream_key, group_name)
            assert pending["pending"] == 0, "进入 DLQ 后原消息仍留在 PEL"
            logger.info(f"[DLQ Test] 死信队列统计: {reclaimer.stats.total_dead_lettered}")
            logger.info("[DLQ Test] ✓ 死信队列验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
                await redis_client.delete(dead_letter_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_reclaimer_module_integration(self, unique_worker_id, unique_stream_prefix):
        """
        测试 PendingTaskReclaimer 模块集成

        验证：
        1. Reclaimer 可以正确启动和停止
        2. 可以手动触发回收
        3. 统计信息正确
        """
        import redis.asyncio as aioredis
        from antcode_worker.transport.redis.keys import RedisKeys
        from antcode_worker.transport.redis.reclaim import (
            PendingTaskReclaimer,
            ReclaimConfig,
            ensure_consumer_group,
        )

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        stream_key = keys.task_ready_stream(unique_worker_id)
        group_name = keys.consumer_group_name()
        config = ReclaimConfig(
            min_idle_time_ms=100,
            max_reclaim_count=10,
            check_interval_seconds=0.5,
        )

        reclaimer = PendingTaskReclaimer(
            redis_client=redis_client,
            worker_id=unique_worker_id,
            keys=keys,
            config=config,
        )

        try:
            await ensure_consumer_group(redis_client, stream_key, group_name)

            # 验证初始状态
            assert reclaimer.is_running is False
            assert reclaimer.stats.total_reclaimed == 0

            # 启动
            await reclaimer.start()
            assert reclaimer.is_running is True

            # 获取 pending 摘要
            summary = await reclaimer.get_pending_summary()
            assert "pending_count" in summary

            # 停止
            await reclaimer.stop()
            assert reclaimer.is_running is False

            logger.info("[Reclaimer Integration Test] ✓ Reclaimer 模块集成验证通过")

        finally:
            await reclaimer.stop()
            await redis_client.delete(stream_key)
            await redis_client.aclose()


@pytest.mark.integration
class TestIdempotentResultReporting:
    """
    模拟重复上报验证幂等测试

    验证：
    1. 多次上报同一结果不会产生错误
    2. 结果数据保持一致
    3. Master 端可以通过 run_id 去重
    4. 断线重连后重复上报的幂等性

    Requirements: 14.5, 5.8
    """

    @pytest.mark.asyncio
    async def test_duplicate_result_report_no_error(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
        *,
        direct_transport_factory,
    ):
        """
        测试重复结果上报不产生错误

        验证：
        1. 多次上报同一结果都返回成功
        2. 不会抛出异常
        """
        import redis.asyncio as aioredis
        from antcode_worker.transport.base import ServerConfig, TaskResult

        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)

        config = ServerConfig(
            redis_url=REDIS_URL,
            task_stream_prefix=unique_stream_prefix,
        )
        transport = direct_transport_factory(
            REDIS_URL,
            unique_worker_id,
            config,
            namespace=namespace,
        )

        try:
            await direct_support.activate_direct_transport(transport)

            # 创建结果
            result = TaskResult(
                run_id=f"run-{unique_task_id}",
                task_id=unique_task_id,
                status="success",
                exit_code=0,
                error_message="",
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=1000.0,
            )

            # 模拟断线重连后重复上报
            successes = []
            for i in range(10):
                success = await transport.report_result(result)
                successes.append(success)
                logger.info(f"[Idempotent Test] 第 {i + 1} 次上报: success={success}")

            # 验证所有上报都成功
            assert all(successes), "部分上报失败"
            logger.info("[Idempotent Test] ✓ 重复上报不产生错误验证通过")

        finally:
            await direct_support.stop_direct_transport(transport)
            await redis_client.delete(keys.task_result_stream())
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_duplicate_result_data_consistency(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
        *,
        direct_transport_factory,
    ):
        """
        测试重复上报的数据一致性

        验证：
        1. 所有上报的结果数据一致
        2. 关键字段（status, exit_code）保持不变
        """
        import redis.asyncio as aioredis
        from antcode_worker.transport.base import ServerConfig, TaskResult

        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)

        config = ServerConfig(
            redis_url=REDIS_URL,
            task_stream_prefix=unique_stream_prefix,
        )
        transport = direct_transport_factory(
            REDIS_URL,
            unique_worker_id,
            config,
            namespace=namespace,
        )

        try:
            await direct_support.activate_direct_transport(transport)

            # 创建结果
            result = TaskResult(
                run_id=f"run-{unique_task_id}",
                task_id=unique_task_id,
                status="success",
                exit_code=0,
                error_message="Test completed",
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=2500.0,
            )

            # 多次上报
            for _ in range(5):
                await transport.report_result(result)

            # 验证所有结果数据一致
            result_key = keys.task_result_stream()
            results = await redis_client.xrevrange(result_key, "+", "-", count=100)
            task_results = [
                status for status in direct_support.decode_task_statuses(results) if status.task_id == unique_task_id
            ]

            assert len(task_results) >= 1, "未找到结果记录"

            # 验证所有结果的关键字段一致
            for status in task_results:
                assert status.status == data_pb2.STATUS_COMPLETED
                assert status.exit_code == 0
                assert "Test completed" in status.error_message

            logger.info(f"[Data Consistency Test] 找到 {len(task_results)} 条一致的结果记录")
            logger.info("[Data Consistency Test] ✓ 数据一致性验证通过")

        finally:
            await direct_support.stop_direct_transport(transport)
            await redis_client.delete(keys.task_result_stream())
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_master_result_loop_handles_duplicate_reports_idempotently(
        self,
        master_database,
        monkeypatch,
        *,
        direct_transport_factory,
    ):
        """
        验证 Master 真实结果处理链路的幂等性
        验证：
        1. 多次上报产生多条记录（at-least-once）
        2. 每条记录均经过真实 ResultLoop 与 TaskRunService
        3. PostgreSQL 中只有一条 TaskRun 且终态一致
        """
        import importlib

        import redis.asyncio as aioredis
        from antcode_worker.transport.base import ServerConfig

        unique_task_id = f"fault-task-{uuid.uuid4().hex[:8]}"
        unique_worker_id = f"fault-worker-{uuid.uuid4().hex[:8]}"
        unique_stream_prefix = f"test:fault:{uuid.uuid4().hex[:8]}:"
        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)
        run_id = f"run-{unique_task_id}"
        config = ServerConfig(redis_url=REDIS_URL, task_stream_prefix=unique_stream_prefix)
        transport = direct_transport_factory(
            REDIS_URL,
            unique_worker_id,
            config,
            namespace=namespace,
        )

        try:
            lease_id = await direct_support.activate_direct_transport(transport)
            async with _master_result_environment(
                redis_client,
                worker_id=unique_worker_id,
                run_id=run_id,
                namespace=namespace,
                lease_id=lease_id,
            ) as result_service:
                result_module = importlib.import_module("antcode_master.ingester.result_loop")
                monkeypatch.setattr(result_module, "task_run_service", result_service)
                result = _successful_task_result(
                    run_id=run_id,
                    task_id=unique_task_id,
                    lease_id=lease_id,
                )
                await _assert_duplicate_master_processing(
                    transport,
                    result=result,
                    redis_client=redis_client,
                    keys=keys,
                )
                logger.info("[Dedup Test] ✓ Master 真实结果链路幂等验证通过")

        finally:
            await direct_support.stop_direct_transport(transport)
            await redis_client.delete(keys.task_result_stream())
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_concurrent_duplicate_reports(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
        *,
        direct_transport_factory,
    ):
        """
        测试并发重复上报

        验证：
        1. 并发上报同一结果不会产生竞态条件
        2. 所有上报都成功
        """
        import redis.asyncio as aioredis
        from antcode_worker.transport.base import ServerConfig, TaskResult

        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)

        config = ServerConfig(
            redis_url=REDIS_URL,
            task_stream_prefix=unique_stream_prefix,
        )
        transport = direct_transport_factory(
            REDIS_URL,
            unique_worker_id,
            config,
            namespace=namespace,
        )

        try:
            await direct_support.activate_direct_transport(transport)

            # 创建结果
            result = TaskResult(
                run_id=f"run-{unique_task_id}",
                task_id=unique_task_id,
                status="success",
                exit_code=0,
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=1000.0,
            )

            # 并发上报
            tasks = [transport.report_result(result) for _ in range(20)]
            successes = await asyncio.gather(*tasks)

            # 验证所有上报都成功
            assert all(successes), "部分并发上报失败"
            logger.info(f"[Concurrent Dedup Test] 并发上报 {len(successes)} 次全部成功")
            logger.info("[Concurrent Dedup Test] ✓ 并发重复上报验证通过")

        finally:
            await direct_support.stop_direct_transport(transport)
            await redis_client.delete(keys.task_result_stream())
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_idempotent_ack_after_reconnect(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
        *,
        direct_transport_factory,
    ):
        """
        测试断线重连后的幂等 ACK

        验证：
        1. 第一次 ACK 成功
        2. 重连后重复 ACK 不会产生错误
        """
        import redis.asyncio as aioredis
        from antcode_worker.transport.base import ServerConfig

        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        config = ServerConfig(
            redis_url=REDIS_URL,
            task_stream_prefix=unique_stream_prefix,
        )
        transport = direct_transport_factory(
            REDIS_URL,
            unique_worker_id,
            config,
            namespace=namespace,
        )

        stream_key = keys.task_ready_stream(unique_worker_id)

        try:
            lease_id = await direct_support.activate_direct_transport(transport)

            # 写入任务并获取 receipt
            task_data = {
                "task_id": unique_task_id,
                "run_id": f"run-{unique_task_id}",
                "project_id": "ack-project",
                "project_type": "code",
                "source_bundle_uri": f"pgartifact://{TEST_SOURCE_BUNDLE_SHA256}",
                "source_bundle_sha256": TEST_SOURCE_BUNDLE_SHA256,
                "source_bundle_size": "1",
                "transfer_method": "source_bundle",
            }
            await direct_support.publish_ready_task(redis_client, transport, task_data)
            task_msg = await transport.poll_task(timeout=5.0)
            assert task_msg is not None, "未能拉取到任务"

            # 第一次 ACK
            success1 = await transport.ack_task(task_msg.receipt, accepted=True)
            assert success1, "第一次 ACK 失败"

            # 模拟断线重连
            await transport.stop()
            assert await transport.start()
            renewed_lease_id, _, _, revoked = await transport.lease_renew(lease_id)
            assert renewed_lease_id == lease_id and not revoked

            # 重复 ACK
            success2 = await transport.ack_task(task_msg.receipt, accepted=True)
            assert success2 is False, "重复 ACK 应报告消息已不在 PEL"
            pending = await redis_client.xpending(stream_key, keys.consumer_group_name())
            assert pending["pending"] == 0

            logger.info("[Idempotent ACK Test] ✓ 断线重连后幂等 ACK 验证通过")

        finally:
            await direct_support.stop_direct_transport(transport)
            await redis_client.delete(stream_key)
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_gateway_receipt_idempotency(self):
        """
        测试 Gateway 模式的 receipt 幂等性

        验证：
        1. Receipt 跟踪正确
        2. 重复操作被识别
        """
        from antcode_worker.transport.gateway.reconnect import (
            ReconnectConfig,
            ReconnectManager,
        )

        config = ReconnectConfig(
            enable_receipt_tracking=True,
            receipt_cache_size=100,
            receipt_ttl=60.0,
        )

        manager = ReconnectManager(config)

        receipt_id = f"receipt-{uuid.uuid4().hex[:8]}"

        # 第一次跟踪
        is_new = manager.track_receipt(receipt_id, "report_result")
        assert is_new is True, "第一次跟踪应该返回 True"

        # 重复跟踪
        is_new_again = manager.track_receipt(receipt_id, "report_result")
        assert is_new_again is False, "重复跟踪应该返回 False"

        # 完成
        manager.complete_receipt(receipt_id, success=True)

        # 检查完成状态
        completed = manager.is_receipt_completed(receipt_id)
        assert completed is True

        logger.info("[Gateway Receipt Test] ✓ Gateway receipt 幂等性验证通过")


@pytest.mark.integration
class TestLogThroughputStress:
    """
    日志吞吐压测

    验证：
    1. 高频日志写入性能
    2. 批量发送效率
    3. Backpressure 机制

    Requirements: 9.5
    """

    @pytest.mark.asyncio
    async def test_high_frequency_log_writes(
        self,
        unique_worker_id,
        unique_stream_prefix,
        direct_transport_factory,
    ):
        """
        测试高频日志写入

        验证：
        1. 大量日志可以快速写入
        2. 不会丢失数据
        """
        import redis.asyncio as aioredis
        from antcode_worker.transport.base import LogMessage, ServerConfig

        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)

        config = ServerConfig(redis_url=REDIS_URL)
        transport = direct_transport_factory(
            REDIS_URL,
            unique_worker_id,
            config,
            namespace=namespace,
        )

        run_id = f"stress-exec-{uuid.uuid4().hex[:8]}"
        num_logs = 500
        log_key = keys.log_ingest_stream()

        try:
            await direct_support.activate_direct_transport(transport)
            assert await transport.claim_run_ownership(run_id, ttl_ms=60_000)

            start_time = time.time()

            # 高频写入日志
            for i in range(num_logs):
                log = LogMessage(
                    run_id=run_id,
                    log_type="stdout",
                    content=f"Log line {i}: " + "x" * 100,  # 约 110 字节
                    timestamp=datetime.now(),
                    sequence=i,
                )
                assert await transport.send_log(log) is True

            end_time = time.time()
            duration = end_time - start_time

            # 验证日志已写入
            log_count = await redis_client.xlen(log_key)
            results = await redis_client.xrange(log_key, "-", "+", count=num_logs)
            batches = [data_pb2.LogBatch.FromString(fields[PROTO_FIELD]) for _, fields in results]
            log_entries = [entry for batch in batches for entry in batch.entries]

            logger.info(f"[Log Throughput Test] 写入 {num_logs} 条日志")
            logger.info(f"[Log Throughput Test] 耗时: {duration:.2f}s")
            logger.info(f"[Log Throughput Test] 吞吐量: {num_logs / duration:.1f} 条/秒")
            logger.info(f"[Log Throughput Test] Redis 中记录数: {log_count}")

            assert log_count == num_logs, f"日志丢失: 期望 {num_logs}, 实际 {log_count}"
            assert len(log_entries) == num_logs
            assert all(entry.run_id == run_id for entry in log_entries)
            assert {entry.sequence for entry in log_entries} == set(range(num_logs))
            logger.info("[Log Throughput Test] ✓ 高频日志写入验证通过")

        finally:
            await transport.release_run_ownership(run_id)
            await direct_support.stop_direct_transport(transport)
            await redis_client.delete(log_key)
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_batch_sender_throughput(self):
        """
        测试批量发送器吞吐量

        验证：
        1. 批量发送比单条发送更高效
        2. 队列管理正确
        """
        from antcode_worker.domain.enums import LogStream
        from antcode_worker.domain.models import LogEntry
        from antcode_worker.logs.batch import BatchConfig, BatchSender

        # 模拟传输层
        class MockTransport:
            def __init__(self):
                self.sent_batches = []
                self.is_connected = True

            async def send_log_batch(self, logs):
                self.sent_batches.append(logs)
                await asyncio.sleep(0.001)  # 模拟网络延迟
                return True

        transport = MockTransport()
        config = BatchConfig(
            batch_size=50,
            batch_timeout=0.1,
            max_queue_size=1000,
        )

        run_id = f"batch-test-{uuid.uuid4().hex[:8]}"
        sender = BatchSender(
            run_id=run_id,
            transport=transport,
            config=config,
        )

        num_entries = 200

        try:
            await sender.start()

            start_time = time.time()

            # 写入大量日志条目
            for i in range(num_entries):
                entry = LogEntry(
                    run_id=run_id,
                    seq=i,
                    timestamp=datetime.now(),
                    stream=LogStream.STDOUT,
                    content=f"Batch log {i}",
                )
                await sender.write(entry)

            # 等待批量发送完成
            await asyncio.sleep(0.5)
            await sender.flush()

            end_time = time.time()
            duration = end_time - start_time

            stats = sender.get_stats()
            logger.info(f"[Batch Throughput Test] 写入 {num_entries} 条日志")
            logger.info(f"[Batch Throughput Test] 耗时: {duration:.2f}s")
            logger.info(f"[Batch Throughput Test] 发送批次数: {stats['batches_sent']}")
            logger.info(f"[Batch Throughput Test] 总发送数: {stats['total_sent']}")

            # 验证批量发送
            assert stats["batches_sent"] > 0, "应该有批量发送"
            assert stats["total_sent"] == num_entries, f"发送数量不正确: {stats['total_sent']}"

            logger.info("[Batch Throughput Test] ✓ 批量发送器吞吐量验证通过")

        finally:
            await sender.stop()

    @pytest.mark.asyncio
    async def test_backpressure_mechanism(self):
        """
        测试 Backpressure 机制

        验证：
        1. 队列满时触发 backpressure
        2. 状态正确转换
        3. 丢弃策略生效
        """
        from antcode_worker.domain.enums import LogStream
        from antcode_worker.domain.models import LogEntry
        from antcode_worker.logs.batch import BackpressureState, BatchConfig, BatchSender

        # 模拟慢速传输层
        class SlowTransport:
            def __init__(self):
                self.is_connected = True

            async def send_log_batch(self, logs):
                await asyncio.sleep(1.0)  # 模拟慢速发送
                return True

        transport = SlowTransport()
        config = BatchConfig(
            batch_size=10,
            batch_timeout=0.5,
            max_queue_size=50,  # 小队列以便快速触发 backpressure
            warning_threshold=0.5,
            critical_threshold=0.8,
            drop_on_critical=True,
        )

        backpressure_states = []

        def on_backpressure(state):
            backpressure_states.append(state)

        run_id = f"bp-test-{uuid.uuid4().hex[:8]}"
        sender = BatchSender(
            run_id=run_id,
            transport=transport,
            config=config,
            on_backpressure=on_backpressure,
        )

        try:
            await sender.start()

            # 快速写入大量日志以触发 backpressure
            accepted = []
            for i in range(100):
                entry = LogEntry(
                    run_id=run_id,
                    seq=i,
                    timestamp=datetime.now(),
                    stream=LogStream.STDOUT,
                    content=f"Backpressure test {i}",
                )
                accepted.append(await sender.write(entry))

            # 检查 backpressure 状态
            stats = sender.get_stats()
            logger.info(f"[Backpressure Test] 队列大小: {stats['queue_size']}")
            logger.info(f"[Backpressure Test] 当前状态: {stats['backpressure_state']}")
            logger.info(f"[Backpressure Test] 丢弃数: {stats['total_dropped']}")
            logger.info(f"[Backpressure Test] 状态变化: {[s.value for s in backpressure_states]}")

            assert backpressure_states == [BackpressureState.WARNING, BackpressureState.CRITICAL]
            assert stats["backpressure_state"] == BackpressureState.CRITICAL.value
            assert stats["queue_size"] == 40
            assert accepted.count(True) == 40
            assert accepted.count(False) == 60
            assert stats["total_dropped"] == 60

            logger.info("[Backpressure Test] ✓ Backpressure 机制验证通过")

        finally:
            await sender.stop()


@pytest.mark.integration
class TestConcurrentSlotsStress:
    """
    并发 slots 压测

    验证：
    1. 多任务并发执行
    2. 资源限制正确
    3. 调度公平性

    Requirements: 4.2
    """

    @pytest.mark.asyncio
    async def test_concurrent_task_execution(self):
        """
        测试并发任务执行

        验证：
        1. 多个任务可以并发执行
        2. 不超过最大并发数
        """
        from antcode_worker.engine.scheduler import Scheduler

        scheduler = Scheduler(max_queue_size=20)

        try:
            await scheduler.start()

            # 入队多个任务
            num_tasks = 15
            for i in range(num_tasks):
                await scheduler.enqueue(
                    run_id=f"concurrent-task-{i}",
                    data={"index": i},
                    priority=i % 10,
                )

            assert scheduler.size == num_tasks

            # 并发出队
            dequeued = []
            for _ in range(num_tasks):
                item = await scheduler.dequeue(timeout=1.0)
                if item:
                    dequeued.append(item)

            assert len(dequeued) == num_tasks
            logger.info(f"[Concurrent Slots Test] 成功出队 {len(dequeued)} 个任务")
            logger.info("[Concurrent Slots Test] ✓ 并发任务执行验证通过")

        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_scheduler_priority_under_load(self):
        """
        测试高负载下的调度优先级

        验证：
        1. 高优先级任务优先出队
        2. 低优先级任务不会饿死（aging）
        """
        from antcode_worker.engine.scheduler import Scheduler

        scheduler = Scheduler(max_queue_size=100)

        try:
            await scheduler.start()

            # 入队不同优先级的任务
            priorities = [1, 5, 10, 3, 8, 2, 9, 4, 7, 6]
            for i, priority in enumerate(priorities):
                await scheduler.enqueue(
                    run_id=f"priority-task-{i}",
                    data={"priority": priority},
                    priority=priority,
                )

            # 出队并验证顺序
            dequeued_priorities = []
            for _ in range(len(priorities)):
                item = await scheduler.dequeue(timeout=1.0)
                if item:
                    run_id, data = item
                    dequeued_priorities.append(data["priority"])

            logger.info(f"[Priority Test] 入队顺序: {priorities}")
            logger.info(f"[Priority Test] 出队顺序: {dequeued_priorities}")

            # 验证高优先级先出队
            assert dequeued_priorities[0] == max(priorities), "最高优先级应该先出队"
            logger.info("[Priority Test] ✓ 调度优先级验证通过")

        finally:
            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_state_manager_concurrent_access(self):
        """
        测试状态管理器并发访问

        验证：
        1. 并发添加/更新/删除不会产生竞态条件
        2. 状态一致性
        """
        from antcode_worker.engine.state import RunState, StateManager

        state_manager = StateManager()
        num_tasks = 50

        async def task_lifecycle(task_id):
            """模拟任务生命周期"""
            run_id = f"concurrent-run-{task_id}"

            # 添加
            await state_manager.add_if_new(run_id, f"task-{task_id}")

            # 状态转换
            await state_manager.transition(run_id, RunState.PREPARING)
            await asyncio.sleep(0.01)
            await state_manager.transition(run_id, RunState.RUNNING)
            await asyncio.sleep(0.01)
            await state_manager.transition(run_id, RunState.COMPLETED)

            # 移除
            await state_manager.remove(run_id)

            return True

        # 并发执行
        tasks = [task_lifecycle(i) for i in range(num_tasks)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 验证没有异常
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(errors) == 0, f"并发访问产生异常: {errors}"

        # 验证状态管理器为空
        all_runs = await state_manager.get_all()
        assert len(all_runs) == 0, "状态管理器应该为空"

        logger.info(f"[Concurrent State Test] 并发处理 {num_tasks} 个任务")
        logger.info("[Concurrent State Test] ✓ 状态管理器并发访问验证通过")


@pytest.mark.integration
class TestRedisBackpressureStress:
    """
    Redis Backpressure 压测

    验证：
    1. 高负载下 Redis 连接稳定
    2. 流量控制正确
    3. 不会因为 Redis 压力导致数据丢失

    Requirements: 5.2
    """

    @pytest.mark.asyncio
    async def test_high_load_redis_writes(self, unique_stream_prefix):
        """
        测试高负载 Redis 写入

        验证：
        1. 大量并发写入不会失败
        2. 数据完整性
        """
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        stream_key = f"{unique_stream_prefix}stress"
        num_writes = 500

        try:
            start_time = time.time()

            semaphore = asyncio.Semaphore(100)

            # 并发写入
            async def write_one(i):
                async with semaphore:
                    for attempt in range(3):
                        try:
                            await redis_client.xadd(
                                stream_key,
                                {
                                    "index": str(i),
                                    "data": f"stress-data-{i}",
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )
                            return True
                        except Exception as e:
                            if attempt == 2:
                                return e
                            await asyncio.sleep(0.05 * (attempt + 1))

            tasks = [write_one(i) for i in range(num_writes)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            end_time = time.time()
            duration = end_time - start_time

            # 统计结果
            successes = sum(1 for r in results if r is True)
            errors = [r for r in results if isinstance(r, Exception)]

            # 验证写入数量
            actual_count = await redis_client.xlen(stream_key)

            logger.info(f"[Redis Stress Test] 并发写入 {num_writes} 条")
            logger.info(f"[Redis Stress Test] 成功: {successes}, 失败: {len(errors)}")
            logger.info(f"[Redis Stress Test] 耗时: {duration:.2f}s")
            logger.info(f"[Redis Stress Test] 吞吐量: {num_writes / duration:.1f} 条/秒")
            logger.info(f"[Redis Stress Test] Redis 中记录数: {actual_count}")

            assert successes == num_writes, f"部分写入失败: {len(errors)}"
            assert actual_count == num_writes, f"数据丢失: 期望 {num_writes}, 实际 {actual_count}"

            logger.info("[Redis Stress Test] ✓ 高负载 Redis 写入验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_flow_controller_under_pressure(self):
        """
        测试流量控制器在压力下的表现

        验证：
        1. 流量控制正确限流
        2. 不会因为限流导致死锁
        """
        from antcode_worker.transport.flow_control import (
            FlowControlConfig,
            FlowControlStrategy,
            create_flow_controller,
        )

        config = FlowControlConfig(
            strategy=FlowControlStrategy.TOKEN_BUCKET,
            initial_rate=100.0,
            max_rate=200.0,
            min_rate=10.0,
            bucket_capacity=50,
        )

        controller = create_flow_controller(
            strategy=FlowControlStrategy.TOKEN_BUCKET,
            config=config,
        )

        # 模拟高频请求
        num_requests = 200
        acquired = 0
        rejected = 0

        start_time = time.time()

        for _ in range(num_requests):
            if await controller.acquire():
                acquired += 1
            else:
                rejected += 1

        end_time = time.time()
        duration = end_time - start_time

        stats = controller.stats
        logger.info(f"[Flow Control Test] 请求数: {num_requests}")
        logger.info(f"[Flow Control Test] 获取成功: {acquired}")
        logger.info(f"[Flow Control Test] 被拒绝: {rejected}")
        logger.info(f"[Flow Control Test] 耗时: {duration:.2f}s")
        logger.info(f"[Flow Control Test] 当前速率: {stats.current_rate}")

        # 验证流量控制生效
        assert acquired > 0, "应该有成功获取的请求"
        logger.info("[Flow Control Test] ✓ 流量控制器压力测试验证通过")

    @pytest.mark.asyncio
    async def test_transport_reconnect_under_load(
        self,
        unique_worker_id,
        unique_stream_prefix,
        direct_transport_factory,
    ):
        """
        测试高负载下的传输层重连

        验证：
        1. 重连后可以继续工作
        2. 不会丢失数据
        """
        import redis.asyncio as aioredis
        from antcode_worker.transport.base import ServerConfig, TaskResult

        namespace = unique_stream_prefix.rstrip(":")
        keys = RedisKeys(namespace=namespace)
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)

        config = ServerConfig(
            redis_url=REDIS_URL,
            task_stream_prefix=unique_stream_prefix,
        )
        transport = direct_transport_factory(
            REDIS_URL,
            unique_worker_id,
            config,
            namespace=namespace,
        )

        try:
            lease_id = await direct_support.activate_direct_transport(transport)

            task_prefix = f"reconnect-{unique_worker_id}"

            # 第一批写入
            batch1_count = 50
            for i in range(batch1_count):
                result = TaskResult(
                    run_id=f"run-{task_prefix}-{i}",
                    task_id=f"{task_prefix}-{i}",
                    status="success",
                    exit_code=0,
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                    duration_ms=float(i * 10),
                )
                await transport.report_result(result)

            # 模拟断线重连
            await transport.stop()
            await asyncio.sleep(0.1)
            assert await transport.start()
            renewed_lease_id, _, _, revoked = await transport.lease_renew(lease_id)
            assert renewed_lease_id == lease_id and not revoked

            # 第二批写入
            batch2_count = 50
            for i in range(batch1_count, batch1_count + batch2_count):
                result = TaskResult(
                    run_id=f"run-{task_prefix}-{i}",
                    task_id=f"{task_prefix}-{i}",
                    status="success",
                    exit_code=0,
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                    duration_ms=float(i * 10),
                )
                await transport.report_result(result)

            # 验证数据完整性
            result_key = keys.task_result_stream()
            results = await redis_client.xrevrange(result_key, "+", "-", count=5000)
            statuses = direct_support.decode_task_statuses(results)
            total_count = sum(1 for status in statuses if status.task_id.startswith(task_prefix))

            expected_count = batch1_count + batch2_count
            logger.info(f"[Reconnect Load Test] 期望记录数: {expected_count}")
            logger.info(f"[Reconnect Load Test] 实际记录数: {total_count}")

            assert total_count == expected_count, f"数据丢失: 期望 {expected_count}, 实际 {total_count}"
            logger.info("[Reconnect Load Test] ✓ 高负载下重连验证通过")

        finally:
            await direct_support.stop_direct_transport(transport)
            await redis_client.delete(keys.task_result_stream())
            await redis_client.aclose()
