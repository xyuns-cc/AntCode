"""
Worker 服务集成测试

使用真实的 Redis 连接验证 Worker 服务功能。

Requirements: 7.1, 7.2, 11.3
"""

import os
import pytest
import uuid

# 从环境变量或默认值获取 Redis URL
REDIS_URL = os.getenv("REDIS_URL", "redis://:redis_i36zi5@154.12.30.182:6379/0")


@pytest.fixture
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"worker-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestDirectModeIntegration:
    """Direct 模式集成测试"""

    @pytest.mark.asyncio
    async def test_redis_transport_connection(self, unique_worker_id):
        """测试 Redis 传输层连接"""
        from antcode_worker.transport import RedisTransport

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)

        # 启动传输层
        result = await transport.start()
        assert result is True, "Redis 传输层启动失败"

        # 验证状态
        status = transport.get_status()
        assert status["mode"] == "direct"
        assert status["running"] is True
        assert transport.is_connected is True

        # 停止传输层
        await transport.stop()
        assert transport.is_running is False

    @pytest.mark.asyncio
    async def test_redis_heartbeat(self, unique_worker_id):
        """测试 Redis 心跳发送"""
        from antcode_worker.transport import RedisTransport, HeartbeatMessage
        from datetime import datetime

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)
        await transport.start()

        try:
            # 发送心跳
            heartbeat = HeartbeatMessage(
                worker_id="test-worker-001",
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
            await transport.stop()

    @pytest.mark.asyncio
    async def test_redis_log_send(self, unique_worker_id):
        """测试 Redis 日志发送"""
        from antcode_worker.transport import RedisTransport, LogMessage
        from datetime import datetime

        transport = RedisTransport(redis_url=REDIS_URL, worker_id=unique_worker_id)
        await transport.start()

        try:
            # 发送日志
            log = LogMessage(
                execution_id="test-execution-001",
                log_type="stdout",
                content="Test log message",
                timestamp=datetime.now(),
                sequence=1,
            )

            result = await transport.send_log(log)
            assert result is True, "日志发送失败"

        finally:
            await transport.stop()


@pytest.mark.integration
class TestWorkerComponents:
    """Worker 组件集成测试"""

    def test_capability_detection(self):
        """测试能力检测"""
        from antcode_worker.heartbeat import CapabilityDetector

        detector = CapabilityDetector()
        capabilities = detector.detect_all()

        assert isinstance(capabilities, dict)
        assert "drissionpage" in capabilities
        assert "curl_cffi" in capabilities

    def test_uv_manager_initialization(self):
        """测试 UV 管理器初始化"""
        from antcode_worker.runtime import UVManager

        manager = UVManager()
        assert manager is not None

    def test_cache_gc_initialization(self):
        """测试缓存 GC 初始化"""
        from antcode_worker.runtime import CacheGC, GCConfig

        config = GCConfig()
        gc = CacheGC(config=config)

        assert gc is not None
        assert gc.config.env_ttl > 0
        assert gc.config.log_ttl > 0

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
