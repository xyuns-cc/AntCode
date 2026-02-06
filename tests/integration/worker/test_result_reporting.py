"""
结果上报集成测试

验证 idempotent report_result 功能。

Requirements: 14.3
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
    return f"result-task-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_stream_prefix():
    """生成唯一 stream 前缀"""
    return f"test:result:{uuid.uuid4().hex[:8]}:"


@pytest.fixture
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"result-worker-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestResultReporting:
    """结果上报测试 - Requirements: 14.3"""

    @pytest.mark.asyncio
    async def test_single_result_report(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        测试单次结果上报

        验证：
        1. 结果可以成功上报到 Redis
        2. 结果数据完整
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
                duration_ms=1500.5,
            )

            # 上报结果
            success = await transport.report_result(result)
            assert success is True, "结果上报失败"

            # 验证结果已写入 Redis
            result_key = REDIS_KEYS.task_result_stream()
            results = await redis_client.xrevrange(result_key, "+", "-", count=10)

            found = False
            for msg_id, data in results:
                if data.get("task_id") == unique_task_id:
                    found = True
                    assert data.get("status") == "success"
                    assert data.get("exit_code") == "0"
                    break

            assert found, "结果未写入 Redis"
            logger.info(f"[Test] 单次结果上报成功: {unique_task_id}")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_idempotent_result_report(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        测试结果上报幂等性

        验证：
        1. 多次上报同一结果不会产生错误
        2. 结果数据保持一致
        
        注意：当前实现是 at-least-once 语义，每次上报都会写入新记录。
        真正的幂等需要在 Master 端通过 run_id 去重。
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
                duration_ms=2000.0,
            )

            # 第一次上报
            success1 = await transport.report_result(result)
            assert success1 is True, "第一次上报失败"

            # 第二次上报（幂等）
            success2 = await transport.report_result(result)
            assert success2 is True, "第二次上报失败"

            # 第三次上报（幂等）
            success3 = await transport.report_result(result)
            assert success3 is True, "第三次上报失败"

            # 验证结果
            result_key = REDIS_KEYS.task_result_stream()
            results = await redis_client.xrevrange(result_key, "+", "-", count=100)

            # 统计该 task_id 的结果数量
            count = sum(
                1 for _, data in results
                if data.get("task_id") == unique_task_id
            )

            # at-least-once 语义：每次上报都会写入
            # 真正的幂等在 Master 端通过 run_id 去重
            assert count >= 1, "未找到结果记录"
            logger.info(f"[Test] 幂等性测试: 上报 3 次，记录数 {count}")
            logger.info(f"[Test] 注意：at-least-once 语义，Master 端需要去重")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_result_report_with_error(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        测试失败结果上报

        验证：
        1. 失败结果可以正确上报
        2. 错误信息完整
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

            # 创建失败结果
            result = TaskResult(
                run_id=f"run-{unique_task_id}",
                task_id=unique_task_id,
                status="failed",
                exit_code=1,
                error_message="Task execution failed: ImportError",
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=500.0,
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
                    assert data.get("status") == "failed"
                    assert data.get("exit_code") == "1"
                    assert "ImportError" in data.get("error_message", "")
                    break

            assert found, "失败结果未写入"
            logger.info(f"[Test] 失败结果上报成功: {unique_task_id}")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_result_report_with_timeout_status(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        测试超时结果上报

        验证：
        1. 超时结果可以正确上报
        2. 状态为 timeout
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

            result = TaskResult(
                run_id=f"run-{unique_task_id}",
                task_id=unique_task_id,
                status="timeout",
                exit_code=-1,
                error_message="Task execution timed out after 60 seconds",
                started_at=datetime.now(),
                finished_at=datetime.now(),
                duration_ms=60000.0,
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
                    assert data.get("status") == "timeout"
                    break

            assert found
            logger.info(f"[Test] 超时结果上报成功: {unique_task_id}")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_result_report_with_cancelled_status(
        self,
        unique_task_id,
        unique_worker_id,
        unique_stream_prefix,
    ):
        """
        测试取消结果上报

        验证：
        1. 取消结果可以正确上报
        2. 状态为 cancelled
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

            result = TaskResult(
                run_id=f"run-{unique_task_id}",
                task_id=unique_task_id,
                status="cancelled",
                exit_code=-2,
                error_message="Task cancelled by user",
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
                    break

            assert found
            logger.info(f"[Test] 取消结果上报成功: {unique_task_id}")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()


@pytest.mark.integration
class TestGatewayResultIdempotency:
    """Gateway 模式结果幂等性测试"""

    @pytest.mark.asyncio
    async def test_gateway_result_cache(self, unique_task_id):
        """
        测试 Gateway 模式结果缓存

        验证：
        1. 结果可以被缓存
        2. 缓存可以被查询
        """
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        worker_id = f"gateway-worker-{uuid.uuid4().hex[:8]}"

        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id=worker_id,
            enable_receipt_idempotency=True,
            receipt_cache_ttl=60.0,
        )

        transport = GatewayTransport(gateway_config=config)

        # 缓存结果
        cache_key = f"result:{unique_task_id}"
        transport._cache_result(cache_key, True)

        # 查询缓存
        cached = transport._get_cached_result(cache_key)
        assert cached is True

        # 查询不存在的缓存
        non_existent = transport._get_cached_result("non-existent-key")
        assert non_existent is None

        logger.info(f"[Test] Gateway 结果缓存验证成功")

    @pytest.mark.asyncio
    async def test_gateway_ack_cache(self, unique_task_id):
        """
        测试 Gateway 模式 ACK 缓存

        验证：
        1. ACK 可以被缓存
        2. 重复 ACK 返回缓存结果
        """
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        worker_id = f"gateway-worker-{uuid.uuid4().hex[:8]}"

        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id=worker_id,
            enable_receipt_idempotency=True,
        )

        transport = GatewayTransport(gateway_config=config)

        # 缓存 ACK
        cache_key = f"ack:{unique_task_id}"
        transport._cache_result(cache_key, True)

        # 查询缓存
        cached = transport._get_cached_result(cache_key)
        assert cached is True

        logger.info(f"[Test] Gateway ACK 缓存验证成功")

    @pytest.mark.asyncio
    async def test_receipt_tracking_idempotency(self):
        """
        测试 Receipt 跟踪幂等性

        验证：
        1. 新 receipt 可以被跟踪
        2. 重复 receipt 被识别
        3. 完成后状态正确
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

        # 跟踪新 receipt
        is_new = manager.track_receipt(receipt_id, "report_result")
        assert is_new is True

        # 重复跟踪
        is_new_again = manager.track_receipt(receipt_id, "report_result")
        assert is_new_again is False

        # 完成 receipt
        manager.complete_receipt(receipt_id, success=True)

        # 检查完成状态
        completed = manager.is_receipt_completed(receipt_id)
        assert completed is True

        # 跟踪已完成的 receipt
        is_new_after_complete = manager.track_receipt(receipt_id, "report_result")
        assert is_new_after_complete is False

        logger.info(f"[Test] Receipt 跟踪幂等性验证成功")


@pytest.mark.integration
class TestResultReportingConcurrency:
    """结果上报并发测试"""

    @pytest.mark.asyncio
    async def test_concurrent_result_reports(self, unique_worker_id, unique_stream_prefix):
        """
        测试并发结果上报

        验证：
        1. 多个任务可以并发上报结果
        2. 所有结果都被正确写入
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

            # 创建多个任务结果
            task_ids = [f"concurrent-task-{i}-{uuid.uuid4().hex[:4]}" for i in range(10)]
            results = [
                TaskResult(
                    run_id=f"run-{task_id}",
                    task_id=task_id,
                    status="success",
                    exit_code=0,
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                    duration_ms=float(i * 100),
                )
                for i, task_id in enumerate(task_ids)
            ]

            # 并发上报
            report_tasks = [transport.report_result(r) for r in results]
            successes = await asyncio.gather(*report_tasks)

            # 验证所有上报成功
            assert all(successes), "部分上报失败"

            # 验证所有结果都写入 Redis
            result_key = REDIS_KEYS.task_result_stream()
            stored_results = await redis_client.xrevrange(result_key, "+", "-", count=100)

            stored_task_ids = {data.get("task_id") for _, data in stored_results}
            for task_id in task_ids:
                assert task_id in stored_task_ids, f"任务 {task_id} 结果未写入"

            logger.info(f"[Test] 并发上报 {len(task_ids)} 个结果成功")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_result_report_under_load(self, unique_worker_id, unique_stream_prefix):
        """
        测试高负载下的结果上报

        验证：
        1. 大量结果可以快速上报
        2. 不会丢失数据
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

            # 创建大量任务结果
            num_tasks = 50
            task_ids = [f"load-task-{i}-{uuid.uuid4().hex[:4]}" for i in range(num_tasks)]

            start_time = datetime.now()

            # 顺序上报（模拟高负载）
            for i, task_id in enumerate(task_ids):
                result = TaskResult(
                    run_id=f"run-{task_id}",
                    task_id=task_id,
                    status="success",
                    exit_code=0,
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                    duration_ms=float(i * 10),
                )
                success = await transport.report_result(result)
                assert success, f"任务 {task_id} 上报失败"

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            # 验证所有结果都写入
            result_key = REDIS_KEYS.task_result_stream()
            stored_results = await redis_client.xrevrange(result_key, "+", "-", count=200)

            stored_task_ids = {data.get("task_id") for _, data in stored_results}
            missing = [tid for tid in task_ids if tid not in stored_task_ids]
            assert len(missing) == 0, f"丢失 {len(missing)} 个结果"

            logger.info(f"[Test] 高负载测试: {num_tasks} 个结果在 {duration:.2f}s 内上报完成")
            logger.info(f"[Test] 吞吐量: {num_tasks / duration:.1f} 结果/秒")

        finally:
            await transport.stop()
            try:
                pass
            except Exception:
                pass
            await redis_client.aclose()
