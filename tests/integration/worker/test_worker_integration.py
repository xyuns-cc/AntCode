"""
Worker 服务集成测试

使用真实的 Redis 连接验证 Worker 服务功能。

Requirements: 7.1, 7.2, 11.3
"""

import os
import uuid

import pytest
from antcode_contracts import data_pb2
from antcode_core.infrastructure.redis.stream_client import PROTO_FIELD
from antcode_worker.transport.redis.keys import RedisKeys

# 集成测试必须显式提供 Redis URL
REDIS_URL = os.getenv("ANTCODE_INTEGRATION_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="ANTCODE_INTEGRATION_REDIS_URL is required for worker integration tests",
)


@pytest.fixture
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"worker-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestDirectModeIntegration:
    """Direct 模式集成测试"""

    @pytest.mark.asyncio
    async def test_redis_transport_connection(self, unique_worker_id, direct_transport_factory):
        """测试 Redis 传输层连接"""
        transport = direct_transport_factory(REDIS_URL, unique_worker_id)

        try:
            result = await transport.start()
            assert result is True, "Redis 传输层启动失败"
            lease_id, _, _, revoked = await transport.lease_renew("")
            assert lease_id and not revoked

            status = transport.get_status()
            assert status["mode"] == "direct"
            assert status["running"] is True
            assert transport.is_connected is True
        finally:
            await transport.deregister("integration-test-cleanup")
            await transport.stop()

        assert transport.is_running is False

    @pytest.mark.asyncio
    async def test_redis_heartbeat(self, unique_worker_id, direct_transport_factory):
        """测试 Redis 心跳发送"""
        from datetime import datetime

        from antcode_worker.transport.base import HeartbeatMessage

        transport = direct_transport_factory(REDIS_URL, unique_worker_id)
        await transport.start()

        try:
            lease_id, _, _, revoked = await transport.lease_renew("")
            assert lease_id and not revoked

            # 发送心跳
            heartbeat = HeartbeatMessage(
                worker_id=unique_worker_id,
                status="online",
                cpu_percent=10.5,
                memory_percent=45.2,
                disk_percent=60.0,
                running_tasks=0,
                max_concurrent_tasks=5,
                timestamp=datetime.now(),
            )

            result = await transport.send_heartbeat(heartbeat)
            assert result is True, "心跳发送失败"

        finally:
            await transport.deregister("integration-test-cleanup")
            await transport.stop()

    @pytest.mark.asyncio
    async def test_redis_log_send(self, unique_worker_id, direct_transport_factory):
        """测试 Redis 日志发送"""
        from datetime import datetime

        import redis.asyncio as aioredis
        from antcode_worker.transport.base import LogMessage

        namespace = f"test:worker:{unique_worker_id}"
        keys = RedisKeys(namespace=namespace)
        redis_client = aioredis.from_url(REDIS_URL, decode_responses=False)
        transport = direct_transport_factory(
            REDIS_URL,
            unique_worker_id,
            namespace=namespace,
        )
        await transport.start()

        try:
            lease_id, _, _, revoked = await transport.lease_renew("")
            assert lease_id and not revoked

            # 发送日志
            log = LogMessage(
                run_id="test-execution-001",
                log_type="stdout",
                content="Test log message",
                timestamp=datetime.now(),
                sequence=1,
            )
            assert await transport.claim_run_ownership(log.run_id, ttl_ms=60_000)

            result = await transport.send_log(log)
            assert result is True, "日志发送失败"

            entries = await redis_client.xrange(keys.log_ingest_stream(), "-", "+")
            assert len(entries) == 1
            batch = data_pb2.LogBatch.FromString(entries[0][1][PROTO_FIELD])
            assert batch.worker_id == unique_worker_id
            assert len(batch.entries) == 1
            assert batch.entries[0].run_id == log.run_id
            assert batch.entries[0].log_type == data_pb2.LOG_TYPE_STDOUT
            assert batch.entries[0].content == log.content

        finally:
            await transport.release_run_ownership("test-execution-001")
            await transport.deregister("integration-test-cleanup")
            await transport.stop()
            await redis_client.delete(keys.log_ingest_stream())
            await redis_client.aclose()


@pytest.mark.integration
class TestWorkerComponents:
    """Worker 组件集成测试"""

    def test_capability_detection(self):
        """测试能力检测"""
        from antcode_worker.heartbeat.capability_detector import CapabilityDetector

        detector = CapabilityDetector()
        capabilities = detector.detect_all()

        assert isinstance(capabilities, dict)
        assert "curl_cffi" in capabilities

    def test_uv_manager_initialization(self):
        """测试 UV 管理器初始化"""
        from antcode_worker.runtime.uv_manager import UVManager

        manager = UVManager()
        assert manager is not None

    def test_cache_gc_initialization(self):
        """测试缓存 GC 初始化"""
        from antcode_worker.runtime.cache_gc import CacheGC, GCConfig

        config = GCConfig()
        gc = CacheGC(config=config)

        assert gc is not None
        assert gc.config.env_ttl > 0
        assert gc.config.temp_ttl > 0

    def test_worker_config_with_redis(self):
        """测试 Worker 配置（使用 Redis URL）"""
        from antcode_worker.config import init_worker_config

        config = init_worker_config(
            name="Integration-Test-Worker",
            port=8002,
            region="test",
            transport_mode="direct",
            redis_url=REDIS_URL,
        )

        assert config.name == "Integration-Test-Worker"
        assert config.transport_mode == "direct"
        assert config.redis_url == REDIS_URL
