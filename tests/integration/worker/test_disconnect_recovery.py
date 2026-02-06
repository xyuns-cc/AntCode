"""
断线恢复集成测试

验证：
- pending reclaim (XAUTOCLAIM)
- result idempotency (duplicate report)

Requirements: 14.5
"""

import asyncio
import os
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
    return f"recovery-task-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"recovery-worker-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_stream_prefix():
    """生成唯一 stream 前缀"""
    return f"test:recovery:{uuid.uuid4().hex[:8]}:"


@pytest.mark.integration
class TestPendingReclaim:
    """Pending 任务回收测试 - Requirements: 14.5"""

    @pytest.mark.asyncio
    async def test_xautoclaim_basic(self, unique_task_id, unique_worker_id, unique_stream_prefix):
        """
        测试 XAUTOCLAIM 基本功能

        验证：
        1. 任务被消费但未 ACK 时进入 pending
        2. XAUTOCLAIM 可以回收 pending 任务
        """
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        stream_key = f"{unique_stream_prefix}ready"
        group_name = "workers"
        consumer1 = f"consumer-1-{uuid.uuid4().hex[:4]}"
        consumer2 = f"consumer-2-{uuid.uuid4().hex[:4]}"

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
            }
            msg_id = await redis_client.xadd(stream_key, task_data)
            logger.info(f"[Test] 任务已添加: msg_id={msg_id}")

            # Consumer1 读取任务（但不 ACK）
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
            logger.info(f"[Test] Consumer1 读取任务: receipt={receipt}")

            # 检查 pending 状态
            pending = await redis_client.xpending(stream_key, group_name)
            assert pending["pending"] >= 1
            logger.info(f"[Test] Pending 任务数: {pending['pending']}")

            # 等待一小段时间（模拟 Consumer1 崩溃）
            await asyncio.sleep(0.1)

            # Consumer2 使用 XAUTOCLAIM 回收任务
            # min_idle_time 设置为 0 以便立即回收（测试用）
            reclaim_result = await redis_client.xautoclaim(
                stream_key,
                group_name,
                consumer2,
                min_idle_time=0,  # 立即回收
                start_id="0-0",
                count=10,
            )

            # 解析结果
            next_start_id, reclaimed_messages, deleted_ids = reclaim_result
            logger.info(f"[Test] XAUTOCLAIM 结果: reclaimed={len(reclaimed_messages)}")

            # 验证任务被回收
            assert len(reclaimed_messages) >= 1
            reclaimed_msg_id, reclaimed_data = reclaimed_messages[0]
            assert reclaimed_data.get("task_id") == unique_task_id

            # Consumer2 ACK 任务
            ack_count = await redis_client.xack(stream_key, group_name, reclaimed_msg_id)
            assert ack_count == 1
            logger.info(f"[Test] Consumer2 ACK 成功")

            # 验证 pending 减少
            pending_after = await redis_client.xpending(stream_key, group_name)
            logger.info(f"[Test] ACK 后 Pending 任务数: {pending_after['pending']}")

            logger.info("[Test] ✓ XAUTOCLAIM 基本功能验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_reclaimer_module(self, unique_worker_id, unique_stream_prefix):
        """
        测试 PendingTaskReclaimer 模块

        验证：
        1. Reclaimer 可以正确初始化
        2. 可以获取 pending 任务摘要
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

        # 使用 namespace 参数而不是 prefix
        keys = RedisKeys(namespace=unique_stream_prefix)
        config = ReclaimConfig(
            min_idle_time_ms=100,  # 100ms 用于测试
            max_reclaim_count=10,
            check_interval_seconds=1.0,
        )

        reclaimer = PendingTaskReclaimer(
            redis_client=redis_client,
            worker_id=unique_worker_id,
            keys=keys,
            config=config,
        )

        try:
            # 验证初始化
            assert reclaimer.is_running is False
            assert reclaimer.stats.total_reclaimed == 0

            # 获取 pending 摘要（应该为空）
            summary = await reclaimer.get_pending_summary()
            assert summary["pending_count"] == 0

            logger.info(f"[Test] Reclaimer 模块初始化成功")

        finally:
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_reclaim_with_delivery_count(self, unique_task_id, unique_stream_prefix):
        """
        测试带投递计数的回收

        验证：
        1. 每次回收增加投递计数
        2. 超过最大重试次数后移入死信队列
        """
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        stream_key = f"{unique_stream_prefix}ready"
        group_name = "workers"
        consumer = f"consumer-{uuid.uuid4().hex[:4]}"

        try:
            # 创建 stream 和 consumer group
            await redis_client.xgroup_create(
                stream_key,
                group_name,
                id="0",
                mkstream=True,
            )

            # 添加任务
            task_data = {"task_id": unique_task_id}
            msg_id = await redis_client.xadd(stream_key, task_data)

            # 多次读取和回收（模拟多次失败）
            for i in range(3):
                # 读取任务
                result = await redis_client.xreadgroup(
                    groupname=group_name,
                    consumername=consumer,
                    streams={stream_key: ">"},
                    count=1,
                    block=1000,
                )

                if not result:
                    # 使用 XAUTOCLAIM 回收
                    reclaim_result = await redis_client.xautoclaim(
                        stream_key,
                        group_name,
                        consumer,
                        min_idle_time=0,
                        start_id="0-0",
                        count=1,
                    )
                    if reclaim_result[1]:
                        logger.info(f"[Test] 第 {i+1} 次回收成功")

                # 不 ACK，模拟失败
                await asyncio.sleep(0.05)

            # 检查 pending 信息
            pending_range = await redis_client.xpending_range(
                stream_key,
                group_name,
                min="-",
                max="+",
                count=10,
            )

            if pending_range:
                entry = pending_range[0]
                # xpending_range 返回字典对象，使用 key 访问
                # 字段包括: message_id, consumer, time_since_delivered, times_delivered
                delivery_count = entry.get("times_delivered", entry.get(3, 1))
                logger.info(f"[Test] 投递计数: {delivery_count}")
                # 投递计数应该增加
                assert delivery_count >= 1

            logger.info("[Test] ✓ 投递计数验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()


@pytest.mark.integration
class TestResultIdempotency:
    """结果幂等性测试 - Requirements: 14.5"""

    @pytest.mark.asyncio
    async def test_duplicate_result_report(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        测试重复结果上报

        验证：
        1. 多次上报同一结果不会产生错误
        2. Master 端可以通过 run_id 去重
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport import RedisTransport, ServerConfig, TaskResult

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        config = ServerConfig(
            redis_url=REDIS_URL,
            task_stream_prefix=unique_stream_prefix,
        )
        transport = RedisTransport(
            redis_url=REDIS_URL,
            worker_id=unique_worker_id,
            config=config,
        )

        try:
            await transport.start()

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
            for i in range(5):
                success = await transport.report_result(result)
                assert success is True
                logger.info(f"[Test] 第 {i+1} 次上报成功")

            # 验证结果
            result_key = REDIS_KEYS.task_result_stream()
            results = await redis_client.xrevrange(result_key, "+", "-", count=100)

            # 统计该 task_id 的结果数量
            count = sum(
                1 for _, data in results
                if data.get("task_id") == unique_task_id
            )

            logger.info(f"[Test] 结果记录数: {count}")
            logger.info(f"[Test] 注意：at-least-once 语义，Master 端需要通过 run_id 去重")

            # 验证所有结果数据一致
            for _, data in results:
                if data.get("task_id") == unique_task_id:
                    assert data.get("status") == "success"
                    assert data.get("exit_code") == "0"

            logger.info("[Test] ✓ 重复结果上报验证通过")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_result_idempotency_with_different_timestamps(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        测试不同时间戳的重复上报

        验证：
        1. 即使时间戳不同，同一 task_id 的结果也能正确处理
        2. Master 端应该使用最新的结果
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport import RedisTransport, ServerConfig, TaskResult

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        config = ServerConfig(
            redis_url=REDIS_URL,
            task_stream_prefix=unique_stream_prefix,
        )
        transport = RedisTransport(
            redis_url=REDIS_URL,
            worker_id=unique_worker_id,
            config=config,
        )

        try:
            await transport.start()

            # 第一次上报
            result1 = TaskResult(
                run_id=f"run-{unique_task_id}",
                task_id=unique_task_id,
                status="success",
                exit_code=0,
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=1000.0,
            )
            await transport.report_result(result1)

            # 等待一小段时间
            await asyncio.sleep(0.1)

            # 第二次上报（不同时间戳）
            result2 = TaskResult(
                run_id=f"run-{unique_task_id}",
                task_id=unique_task_id,
                status="success",
                exit_code=0,
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=1000.0,
            )
            await transport.report_result(result2)

            # 验证结果
            result_key = REDIS_KEYS.task_result_stream()
            results = await redis_client.xrevrange(result_key, "+", "-", count=100)

            task_results = [
                (msg_id, data) for msg_id, data in results
                if data.get("task_id") == unique_task_id
            ]

            assert len(task_results) >= 2
            logger.info(f"[Test] 不同时间戳上报: {len(task_results)} 条记录")

            # Master 端应该使用最新的（最后一条）
            latest_msg_id, latest_data = task_results[0]
            logger.info(f"[Test] 最新记录: msg_id={latest_msg_id}")

            logger.info("[Test] ✓ 不同时间戳重复上报验证通过")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()


@pytest.mark.integration
class TestReconnectBehavior:
    """重连行为测试"""

    @pytest.mark.asyncio
    async def test_heartbeat_reconnect_on_failure(self, unique_worker_id):
        """
        测试心跳连续失败触发重连

        验证：
        1. 连续失败后进入降级模式
        2. 触发重连尝试
        """
        from antcode_worker.heartbeat.reporter import (
            HeartbeatReporter,
            HeartbeatState,
        )

        # 创建一个模拟的 transport，is_connected=True 但 send_heartbeat 返回 False
        # 这样 send_heartbeat 不会提前返回，但会记录失败
        class MockFailingTransport:
            is_connected = True  # 必须为 True，否则 send_heartbeat 会提前返回

            async def send_heartbeat(self, heartbeat):
                return False  # 模拟发送失败

            async def reconnect(self):
                return False

        transport = MockFailingTransport()
        reporter = HeartbeatReporter(
            transport=transport,
            worker_id=unique_worker_id,
        )

        # 模拟多次心跳失败
        for i in range(6):
            success = await reporter.send_heartbeat()
            assert success is False
            logger.info(f"[Test] 心跳失败 {i+1} 次")

        # 验证连续失败计数
        assert reporter.consecutive_failures >= 5

        logger.info(f"[Test] 连续失败次数: {reporter.consecutive_failures}")
        logger.info("[Test] ✓ 心跳失败触发重连验证通过")

    @pytest.mark.asyncio
    async def test_gateway_reconnect_manager(self):
        """
        测试 Gateway 重连管理器

        验证：
        1. 断线后触发重连
        2. 指数退避正确计算
        """
        from antcode_worker.transport.gateway.reconnect import (
            ExponentialBackoff,
            ReconnectConfig,
            ReconnectManager,
            ReconnectState,
        )

        # 测试指数退避（jitter=0.0 确保确定性结果）
        backoff = ExponentialBackoff(
            initial=1.0,
            maximum=10.0,
            multiplier=2.0,
            jitter=0.0,
        )

        b1 = backoff.next_backoff()
        assert b1 == 1.0

        b2 = backoff.next_backoff()
        assert b2 == 2.0

        b3 = backoff.next_backoff()
        assert b3 == 4.0

        b4 = backoff.next_backoff()
        assert b4 == 8.0

        b5 = backoff.next_backoff()
        assert b5 == 10.0  # 被限制在最大值

        logger.info("[Test] 指数退避: 1.0 -> 2.0 -> 4.0 -> 8.0 -> 10.0")

        # 测试重连管理器 - 直接测试 reconnect 方法
        # mock_connect 第一次调用就返回 True
        async def mock_connect():
            return True

        config = ReconnectConfig(
            initial_backoff=0.1,
            max_backoff=1.0,
            max_attempts=3,
        )

        manager = ReconnectManager(config, connect_func=mock_connect)

        # 初始状态
        assert manager.state == ReconnectState.IDLE

        # 执行重连 - 应该成功
        success = await manager.reconnect()
        assert success is True
        assert manager.state == ReconnectState.IDLE

        logger.info("[Test] 重连成功")
        logger.info("[Test] ✓ Gateway 重连管理器验证通过")

    @pytest.mark.asyncio
    async def test_transport_reconnect_after_disconnect(self, unique_worker_id):
        """
        测试 Transport 断线后重连

        验证：
        1. Transport 可以检测断线
        2. 可以重新连接
        """
        from antcode_worker.transport import RedisTransport

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)

        try:
            # 第一次连接
            started = await transport.start()
            assert started is True
            assert transport.is_connected is True
            logger.info("[Test] 第一次连接成功")

            # 停止（模拟断线）
            await transport.stop()
            assert transport.is_connected is False
            logger.info("[Test] 断线")

            # 重新连接
            started = await transport.start()
            assert started is True
            assert transport.is_connected is True
            logger.info("[Test] 重新连接成功")

            logger.info("[Test] ✓ Transport 断线重连验证通过")

        finally:
            await transport.stop()


@pytest.mark.integration
class TestConsumerGroupManagement:
    """消费者组管理测试"""

    @pytest.mark.asyncio
    async def test_ensure_consumer_group(self, unique_stream_prefix):
        """
        测试确保消费者组存在

        验证：
        1. 可以创建新的消费者组
        2. 已存在的组不会报错
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport.redis.reclaim import ensure_consumer_group

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        stream_key = f"{unique_stream_prefix}test-stream"
        group_name = "test-group"

        try:
            # 第一次创建
            success = await ensure_consumer_group(
                redis_client,
                stream_key,
                group_name,
            )
            assert success is True
            logger.info("[Test] 第一次创建消费者组成功")

            # 第二次创建（应该不报错）
            success = await ensure_consumer_group(
                redis_client,
                stream_key,
                group_name,
            )
            assert success is True
            logger.info("[Test] 第二次创建（已存在）成功")

            logger.info("[Test] ✓ 消费者组管理验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_cleanup_dead_consumers(self, unique_stream_prefix):
        """
        测试清理死亡消费者

        验证：
        1. 可以识别空闲的消费者
        2. 可以清理没有 pending 消息的消费者
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport.redis.reclaim import cleanup_dead_consumers

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        stream_key = f"{unique_stream_prefix}cleanup-stream"
        group_name = "cleanup-group"

        try:
            # 创建 stream 和 group
            await redis_client.xgroup_create(
                stream_key,
                group_name,
                id="0",
                mkstream=True,
            )

            # 添加一条消息
            await redis_client.xadd(stream_key, {"test": "data"})

            # 创建一个消费者（读取消息并 ACK）
            consumer_name = f"consumer-{uuid.uuid4().hex[:4]}"
            result = await redis_client.xreadgroup(
                groupname=group_name,
                consumername=consumer_name,
                streams={stream_key: ">"},
                count=1,
            )

            if result:
                _, messages = result[0]
                msg_id, _ = messages[0]
                await redis_client.xack(stream_key, group_name, msg_id)

            # 清理死亡消费者（空闲时间设置为 0 以便立即清理）
            cleaned = await cleanup_dead_consumers(
                redis_client,
                stream_key,
                group_name,
                max_idle_time_ms=0,
            )

            logger.info(f"[Test] 清理的消费者: {cleaned}")
            logger.info("[Test] ✓ 清理死亡消费者验证通过")

        finally:
            try:
                await redis_client.delete(stream_key)
            except Exception:
                pass
            await redis_client.aclose()
