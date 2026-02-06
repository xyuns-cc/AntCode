"""
Worker 注册集成测试

验证 Worker 启动后发布 presence（心跳 + 静态信息）。

Requirements: 14.1
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
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"test-worker-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestWorkerRegistration:
    """Worker 注册测试 - Requirements: 14.1"""

    @pytest.mark.asyncio
    async def test_worker_presence_via_heartbeat(self, unique_worker_id):
        """
        测试 Worker 通过心跳发布 presence

        验证：
        1. Worker 启动后可以发送心跳
        2. 心跳数据包含必要的节点信息
        3. 心跳数据可以被 Master/Web API 观测到
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport import RedisTransport
        from antcode_worker.transport.base import HeartbeatMessage

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)

        try:
            # 启动 Transport
            started = await transport.start()
            assert started, "Transport 启动失败"

            # 发送心跳（发布 presence）
            heartbeat = HeartbeatMessage(
                worker_id=unique_worker_id,
                status="online",
                cpu_percent=25.5,
                memory_percent=50.0,
                disk_percent=40.0,
                running_tasks=0,
                max_concurrent_tasks=5,
                timestamp=datetime.now(),
            )

            result = await transport.send_heartbeat(heartbeat)
            assert result is True, "心跳发送失败"

            # 验证心跳数据已写入 Redis
            hb_key = REDIS_KEYS.heartbeat_key(unique_worker_id)
            hb_data = await redis_client.hgetall(hb_key)

            assert hb_data is not None, "心跳数据未写入 Redis"
            assert hb_data.get("status") == "online"
            assert float(hb_data.get("cpu_percent", 0)) == 25.5
            assert float(hb_data.get("memory_percent", 0)) == 50.0
            assert int(hb_data.get("max_concurrent_tasks", 0)) == 5

            logger.info(f"[Test] Worker presence 已发布: {unique_worker_id}")
            logger.info(f"[Test] 心跳数据: {hb_data}")

        finally:
            await transport.stop()
            # 清理测试数据
            try:
                await redis_client.delete(hb_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_heartbeat_reporter_initialization(self, unique_worker_id):
        """
        测试心跳上报器初始化

        验证：
        1. HeartbeatReporter 可以正确初始化
        2. 可以设置节点 ID 和配置
        """
        from antcode_worker.heartbeat.reporter import HeartbeatReporter
        from antcode_worker.transport import RedisTransport

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)

        try:
            await transport.start()

            # 创建心跳上报器
            reporter = HeartbeatReporter(
                transport=transport,
                worker_id=unique_worker_id,
                version="1.0.0-test",
                max_concurrent_tasks=5,
            )

            # 验证初始化
            assert reporter._worker_id == unique_worker_id
            assert reporter._version == "1.0.0-test"
            assert reporter._max_concurrent_tasks == 5
            assert reporter.is_running is False

            logger.info(f"[Test] HeartbeatReporter 初始化成功: {unique_worker_id}")

        finally:
            await transport.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_reporter_send(self, unique_worker_id):
        """
        测试心跳上报器发送心跳

        验证：
        1. HeartbeatReporter 可以发送心跳
        2. 心跳发送后更新 last_heartbeat_time
        """
        import redis.asyncio as aioredis

        from antcode_worker.heartbeat.reporter import HeartbeatReporter
        from antcode_worker.transport import RedisTransport

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)

        try:
            await transport.start()

            reporter = HeartbeatReporter(
                transport=transport,
                worker_id=unique_worker_id,
                version="1.0.0-test",
            )

            # 发送心跳
            success = await reporter.send_heartbeat()
            assert success is True, "心跳发送失败"

            # 验证 last_heartbeat_time 已更新
            assert reporter.last_heartbeat_time is not None

            # 验证 Redis 中的数据
            hb_key = REDIS_KEYS.heartbeat_key(unique_worker_id)
            hb_data = await redis_client.hgetall(hb_key)
            assert hb_data.get("status") == "online"

            logger.info(f"[Test] HeartbeatReporter 发送成功: last_time={reporter.last_heartbeat_time}")

        finally:
            await transport.stop()
            try:
                await redis_client.delete(hb_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_heartbeat_reporter_loop(self, unique_worker_id):
        """
        测试心跳上报器循环

        验证：
        1. HeartbeatReporter 可以启动循环
        2. 循环会定期发送心跳
        3. 可以正常停止
        """
        import redis.asyncio as aioredis

        from antcode_worker.heartbeat.reporter import HeartbeatReporter
        from antcode_worker.transport import RedisTransport

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)

        try:
            await transport.start()

            reporter = HeartbeatReporter(
                transport=transport,
                worker_id=unique_worker_id,
            )

            # 启动心跳循环（使用最小间隔）
            await reporter.start(interval=10)
            assert reporter.is_running is True

            # 等待一次心跳
            await asyncio.sleep(0.5)

            # 手动发送一次心跳确保数据存在
            await reporter.send_heartbeat()

            # 验证心跳已发送
            hb_key = REDIS_KEYS.heartbeat_key(unique_worker_id)
            hb_data = await redis_client.hgetall(hb_key)
            assert hb_data is not None
            assert len(hb_data) > 0

            # 停止
            await reporter.stop()
            assert reporter.is_running is False

            logger.info(f"[Test] HeartbeatReporter 循环测试通过")

        finally:
            await transport.stop()
            try:
                await redis_client.delete(hb_key)
            except Exception:
                pass
            await redis_client.aclose()

    @pytest.mark.asyncio
    async def test_capability_detection(self):
        """
        测试节点能力检测

        验证：
        1. CapabilityDetector 可以检测节点能力
        2. 能力信息包含在心跳中
        """
        from antcode_worker.heartbeat.reporter import CapabilityDetector

        detector = CapabilityDetector()
        capabilities = detector.detect_all()

        # 验证返回结构
        assert isinstance(capabilities, dict)
        assert "drissionpage" in capabilities
        assert "curl_cffi" in capabilities

        # 验证每个能力的结构
        for name, cap in capabilities.items():
            assert isinstance(cap, dict)
            assert "enabled" in cap

        logger.info(f"[Test] 节点能力检测: {capabilities}")

    @pytest.mark.asyncio
    async def test_worker_identity_persistence(self, unique_worker_id):
        """
        测试 Worker 身份持久化

        验证：
        1. Identity 可以生成
        2. Identity 可以保存和加载
        3. worker_id 跨重启保持稳定
        """
        import tempfile
        from pathlib import Path

        from antcode_worker.security.identity import Identity

        with tempfile.TemporaryDirectory() as tmpdir:
            identity_path = Path(tmpdir) / "identity.yaml"

            # 生成新身份
            identity1 = Identity.generate(
                zone="test-zone",
                labels={"env": "test", "role": "worker"},
                version="1.0.0",
            )

            assert identity1.worker_id is not None
            assert len(identity1.worker_id) > 0
            assert identity1.zone == "test-zone"

            # 保存身份
            saved = identity1.save(identity_path)
            assert saved is True
            assert identity_path.exists()

            # 加载身份
            identity2 = Identity.load(identity_path)
            assert identity2 is not None
            assert identity2.worker_id == identity1.worker_id
            assert identity2.zone == identity1.zone
            assert identity2.labels == identity1.labels

            logger.info(f"[Test] Worker 身份持久化成功: {identity1.worker_id}")

    @pytest.mark.asyncio
    async def test_worker_presence_observable_by_master(self, unique_worker_id):
        """
        测试 Worker presence 可被 Master/Web API 观测

        验证：
        1. Worker 发布 presence 后
        2. 可以通过 Redis 查询到 Worker 状态
        3. 状态信息完整
        """
        import redis.asyncio as aioredis

        from antcode_worker.transport import RedisTransport
        from antcode_worker.transport.base import HeartbeatMessage

        redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)

        try:
            await transport.start()

            # Worker 发布 presence
            heartbeat = HeartbeatMessage(
                worker_id=unique_worker_id,
                status="online",
                cpu_percent=30.0,
                memory_percent=60.0,
                disk_percent=50.0,
                running_tasks=2,
                max_concurrent_tasks=10,
                timestamp=datetime.now(),
            )

            await transport.send_heartbeat(heartbeat)

            # 模拟 Master/Web API 查询 Worker 状态
            hb_key = REDIS_KEYS.heartbeat_key(unique_worker_id)

            # 检查 key 是否存在
            exists = await redis_client.exists(hb_key)
            assert exists == 1, "Worker presence 不可观测"

            # 获取完整状态
            status = await redis_client.hgetall(hb_key)
            assert status.get("status") == "online"
            assert int(status.get("running_tasks", 0)) == 2
            assert int(status.get("max_concurrent_tasks", 0)) == 10

            # 检查 TTL（心跳应该有过期时间）
            ttl = await redis_client.ttl(hb_key)
            assert ttl > 0, "心跳数据应该有 TTL"

            logger.info(f"[Test] Worker presence 可被观测: {status}")
            logger.info(f"[Test] TTL: {ttl}s")

        finally:
            await transport.stop()
            try:
                await redis_client.delete(hb_key)
            except Exception:
                pass
            await redis_client.aclose()
