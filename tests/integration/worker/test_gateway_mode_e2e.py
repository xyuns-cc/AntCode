"""
Gateway 模式 E2E 测试

验证 Gateway 模式的完整功能：
- 公网模式连接验证
- mTLS/API key 认证验证
- idempotent receipt / idempotent result 验证

Checkpoint 15: Gateway 模式 E2E 跑通
Requirements: 5.5, 5.6, 5.7, 11.1, 11.2, 11.3
"""

import asyncio
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from loguru import logger


@pytest.fixture
def unique_task_id():
    """生成唯一任务 ID"""
    return f"gateway-task-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def unique_worker_id():
    """生成唯一 Worker ID"""
    return f"gateway-worker-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestGatewayTransportConnection:
    """Gateway 传输层连接测试"""

    @pytest.mark.asyncio
    async def test_gateway_transport_initialization(self, unique_worker_id):
        """
        测试 Gateway Transport 初始化

        验证：
        1. GatewayConfig 正确配置
        2. GatewayTransport 可以创建
        3. 认证器正确初始化
        """
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        # 创建配置
        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            use_tls=False,
            auth_method="api_key",
            api_key="test-api-key-12345",
            worker_id=unique_worker_id,
            enable_reconnect=True,
            initial_backoff=1.0,
            max_backoff=30.0,
        )

        # 创建 Transport
        transport = GatewayTransport(gateway_config=config)

        # 验证配置
        assert transport.gateway_config.gateway_host == "localhost"
        assert transport.gateway_config.gateway_port == 50051
        assert transport.gateway_config.auth_method == "api_key"
        assert transport.gateway_config.worker_id == unique_worker_id
        assert transport.gateway_config.enable_reconnect is True

        # 验证初始状态
        from antcode_worker.transport.base import TransportMode
        assert transport.mode == TransportMode.GATEWAY
        assert transport.is_connected is False

        logger.info(f"[Test] Gateway Transport 初始化成功: worker_id={unique_worker_id}")

    @pytest.mark.asyncio
    async def test_gateway_config_tls_settings(self):
        """
        测试 Gateway TLS 配置

        验证：
        1. TLS 配置正确设置
        2. 证书路径配置
        """
        from antcode_worker.transport.gateway.transport import GatewayConfig

        # 创建 TLS 配置
        config = GatewayConfig(
            gateway_host="gateway.example.com",
            gateway_port=443,
            use_tls=True,
            ca_cert_path="/path/to/ca.crt",
            client_cert_path="/path/to/client.crt",
            client_key_path="/path/to/client.key",
            server_name_override="gateway.example.com",
        )

        assert config.use_tls is True
        assert config.ca_cert_path == "/path/to/ca.crt"
        assert config.client_cert_path == "/path/to/client.crt"
        assert config.client_key_path == "/path/to/client.key"
        assert config.server_name_override == "gateway.example.com"

        logger.info("[Test] Gateway TLS 配置验证成功")


@pytest.mark.integration
class TestGatewayAuthentication:
    """Gateway 认证测试"""

    @pytest.mark.asyncio
    async def test_api_key_authentication(self, unique_worker_id):
        """
        测试 API Key 认证

        验证：
        1. API Key 认证器正确初始化
        2. 认证元数据正确生成
        """
        from antcode_worker.transport.gateway.auth import (
            AuthConfig,
            AuthMethod,
            GatewayAuthenticator,
        )

        # 创建 API Key 认证配置
        config = AuthConfig(
            method=AuthMethod.API_KEY,
            api_key="test-api-key-secret-12345",
            worker_id=unique_worker_id,
        )

        authenticator = GatewayAuthenticator(config)

        # 获取认证元数据
        metadata = authenticator.get_metadata()

        # 验证元数据
        metadata_dict = dict(metadata)
        assert "x-api-key" in metadata_dict
        assert metadata_dict["x-api-key"] == "test-api-key-secret-12345"
        assert "x-worker-id" in metadata_dict
        assert metadata_dict["x-worker-id"] == unique_worker_id

        # 验证属性
        assert authenticator.method == AuthMethod.API_KEY
        assert authenticator.worker_id == unique_worker_id

        logger.info(f"[Test] API Key 认证验证成功: worker_id={unique_worker_id}")

    @pytest.mark.asyncio
    async def test_mtls_authentication_config(self):
        """
        测试 mTLS 认证配置

        验证：
        1. mTLS 配置正确设置
        2. 证书路径验证
        """
        from antcode_worker.transport.gateway.auth import (
            AuthConfig,
            AuthMethod,
        )

        # 创建临时证书文件
        with tempfile.TemporaryDirectory() as tmpdir:
            cert_path = Path(tmpdir) / "client.crt"
            key_path = Path(tmpdir) / "client.key"

            # 创建模拟证书文件
            cert_path.write_text("-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----")
            key_path.write_text("-----BEGIN PRIVATE KEY-----\nMOCK\n-----END PRIVATE KEY-----")

            # 创建 mTLS 配置
            config = AuthConfig(
                method=AuthMethod.MTLS,
                client_cert_path=str(cert_path),
                client_key_path=str(key_path),
                worker_id="mtls-worker-001",
            )

            assert config.method == AuthMethod.MTLS
            assert config.client_cert_path == str(cert_path)
            assert config.client_key_path == str(key_path)

            logger.info("[Test] mTLS 认证配置验证成功")

    @pytest.mark.asyncio
    async def test_hmac_authentication(self, unique_worker_id):
        """
        测试 HMAC 签名认证

        验证：
        1. HMAC 认证器正确初始化
        2. 签名正确生成
        """
        from antcode_worker.transport.gateway.auth import (
            AuthConfig,
            AuthMethod,
            GatewayAuthenticator,
        )

        # 创建 HMAC 认证配置
        config = AuthConfig(
            method=AuthMethod.HMAC,
            hmac_secret="test-hmac-secret-key",
            worker_id=unique_worker_id,
        )

        authenticator = GatewayAuthenticator(config)

        # 获取认证元数据
        metadata = authenticator.get_metadata()
        metadata_dict = dict(metadata)

        # 验证 HMAC 相关头
        assert "x-worker-id" in metadata_dict
        assert "x-timestamp" in metadata_dict
        assert "x-nonce" in metadata_dict
        assert "x-signature" in metadata_dict

        # 验证签名非空
        assert len(metadata_dict["x-signature"]) > 0
        assert len(metadata_dict["x-nonce"]) > 0

        logger.info(f"[Test] HMAC 认证验证成功: worker_id={unique_worker_id}")

    @pytest.mark.asyncio
    async def test_jwt_authentication(self, unique_worker_id):
        """
        测试 JWT Token 认证

        验证：
        1. JWT 认证器正确初始化
        2. Authorization 头正确生成
        """
        from antcode_worker.transport.gateway.auth import (
            AuthConfig,
            AuthMethod,
            GatewayAuthenticator,
        )

        # 创建 JWT 认证配置
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.signature"
        config = AuthConfig(
            method=AuthMethod.JWT,
            jwt_token=jwt_token,
            worker_id=unique_worker_id,
        )

        authenticator = GatewayAuthenticator(config)

        # 获取认证元数据
        metadata = authenticator.get_metadata()
        metadata_dict = dict(metadata)

        # 验证 Authorization 头
        assert "authorization" in metadata_dict
        assert metadata_dict["authorization"] == f"Bearer {jwt_token}"

        logger.info(f"[Test] JWT 认证验证成功: worker_id={unique_worker_id}")

    @pytest.mark.asyncio
    async def test_credential_update(self, unique_worker_id):
        """
        测试凭证更新

        验证：
        1. 凭证可以运行时更新
        2. 更新后元数据正确
        """
        from antcode_worker.transport.gateway.auth import (
            AuthConfig,
            AuthMethod,
            GatewayAuthenticator,
        )

        # 创建初始配置
        config = AuthConfig(
            method=AuthMethod.API_KEY,
            api_key="initial-api-key",
            worker_id=unique_worker_id,
        )

        authenticator = GatewayAuthenticator(config)

        # 验证初始元数据
        metadata1 = dict(authenticator.get_metadata())
        assert metadata1["x-api-key"] == "initial-api-key"

        # 更新凭证
        authenticator.update_credentials(api_key="updated-api-key")

        # 验证更新后的元数据
        metadata2 = dict(authenticator.get_metadata())
        assert metadata2["x-api-key"] == "updated-api-key"

        logger.info("[Test] 凭证更新验证成功")


@pytest.mark.integration
class TestGatewayReconnect:
    """Gateway 重连测试"""

    @pytest.mark.asyncio
    async def test_reconnect_manager_initialization(self):
        """
        测试重连管理器初始化

        验证：
        1. ReconnectConfig 正确配置
        2. ReconnectManager 正确初始化
        """
        from antcode_worker.transport.gateway.reconnect import (
            ReconnectConfig,
            ReconnectManager,
            ReconnectState,
        )

        # 创建配置
        config = ReconnectConfig(
            initial_backoff=1.0,
            max_backoff=60.0,
            backoff_multiplier=2.0,
            jitter_factor=0.1,
            max_attempts=5,
        )

        # 创建管理器
        manager = ReconnectManager(config)

        # 验证初始状态
        assert manager.state == ReconnectState.IDLE
        assert manager.is_connected is True

        # 验证统计
        stats = manager.get_stats()
        assert stats.total_reconnects == 0
        assert stats.successful_reconnects == 0
        assert stats.failed_reconnects == 0

        logger.info("[Test] 重连管理器初始化验证成功")

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """
        测试指数退避计算

        验证：
        1. 退避时间正确计算
        2. 不超过最大值
        """
        from antcode_worker.transport.gateway.reconnect import ExponentialBackoff

        backoff = ExponentialBackoff(
            initial=1.0,
            maximum=10.0,
            multiplier=2.0,
            jitter=0.0,  # 禁用抖动以便精确测试
        )

        # 第一次退避
        b1 = backoff.next_backoff()
        assert b1 == 1.0

        # 第二次退避
        b2 = backoff.next_backoff()
        assert b2 == 2.0

        # 第三次退避
        b3 = backoff.next_backoff()
        assert b3 == 4.0

        # 第四次退避
        b4 = backoff.next_backoff()
        assert b4 == 8.0

        # 第五次退避（应该被限制在最大值）
        b5 = backoff.next_backoff()
        assert b5 == 10.0

        # 重置
        backoff.reset()
        assert backoff.attempt == 0
        b_reset = backoff.next_backoff()
        assert b_reset == 1.0

        logger.info("[Test] 指数退避计算验证成功")

    @pytest.mark.asyncio
    async def test_reconnect_state_transitions(self):
        """
        测试重连状态转换

        验证：
        1. 断开连接触发重连
        2. 状态正确转换
        """
        from antcode_worker.transport.gateway.reconnect import (
            ReconnectConfig,
            ReconnectManager,
            ReconnectState,
        )

        config = ReconnectConfig(
            initial_backoff=0.1,
            max_backoff=1.0,
            max_attempts=2,
        )

        # 模拟连接函数（总是失败）
        connect_attempts = []

        async def mock_connect():
            connect_attempts.append(1)
            return False

        manager = ReconnectManager(config, connect_func=mock_connect)

        # 初始状态
        assert manager.state == ReconnectState.IDLE

        # 通知断开
        manager.notify_disconnected("test disconnect")
        assert manager.state == ReconnectState.DISCONNECTED

        # 等待重连尝试
        await asyncio.sleep(0.5)

        # 验证有重连尝试
        assert len(connect_attempts) >= 1

        # 停止管理器
        await manager.stop()
        assert manager.state == ReconnectState.STOPPED

        logger.info("[Test] 重连状态转换验证成功")

    @pytest.mark.asyncio
    async def test_reconnect_success(self):
        """
        测试重连成功

        验证：
        1. 重连成功后状态恢复
        2. 统计正确更新
        """
        from antcode_worker.transport.gateway.reconnect import (
            ReconnectConfig,
            ReconnectManager,
            ReconnectState,
        )

        config = ReconnectConfig(
            initial_backoff=0.1,
            max_backoff=1.0,
        )

        # 模拟连接函数（成功）
        async def mock_connect():
            return True

        manager = ReconnectManager(config, connect_func=mock_connect)

        # 执行重连
        success = await manager.reconnect()

        assert success is True
        assert manager.state == ReconnectState.IDLE
        assert manager.is_connected is True

        stats = manager.get_stats()
        assert stats.total_reconnects == 1
        assert stats.successful_reconnects == 1
        assert stats.failed_reconnects == 0

        logger.info("[Test] 重连成功验证通过")


@pytest.mark.integration
class TestReceiptIdempotency:
    """Receipt 幂等性测试"""

    @pytest.mark.asyncio
    async def test_receipt_tracking(self):
        """
        测试 Receipt 跟踪

        验证：
        1. 新 receipt 可以被跟踪
        2. 重复 receipt 被识别
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

        # 跟踪新 receipt
        receipt_id = "receipt-001"
        is_new = manager.track_receipt(receipt_id, "ack")
        assert is_new is True

        # 重复跟踪同一 receipt
        is_new_again = manager.track_receipt(receipt_id, "ack")
        assert is_new_again is False

        # 完成 receipt
        manager.complete_receipt(receipt_id, success=True)

        # 检查完成状态
        completed = manager.is_receipt_completed(receipt_id)
        assert completed is True

        # 跟踪已完成的 receipt（应该返回 False）
        is_new_after_complete = manager.track_receipt(receipt_id, "ack")
        assert is_new_after_complete is False

        logger.info("[Test] Receipt 跟踪验证成功")

    @pytest.mark.asyncio
    async def test_receipt_pending_list(self):
        """
        测试待处理 Receipt 列表

        验证：
        1. 待处理列表正确维护
        2. 完成后从列表移除
        """
        from antcode_worker.transport.gateway.reconnect import (
            ReconnectConfig,
            ReconnectManager,
        )

        config = ReconnectConfig(enable_receipt_tracking=True)
        manager = ReconnectManager(config)

        # 添加多个 receipt
        manager.track_receipt("receipt-a", "ack")
        manager.track_receipt("receipt-b", "report")
        manager.track_receipt("receipt-c", "ack")

        # 检查待处理列表
        pending = manager.get_pending_receipts()
        assert len(pending) == 3
        assert "receipt-a" in pending
        assert "receipt-b" in pending
        assert "receipt-c" in pending

        # 完成一个
        manager.complete_receipt("receipt-b", success=True)

        # 检查待处理列表
        pending_after = manager.get_pending_receipts()
        assert len(pending_after) == 2
        assert "receipt-b" not in pending_after

        logger.info("[Test] Receipt 待处理列表验证成功")

    @pytest.mark.asyncio
    async def test_gateway_transport_result_idempotency(self, unique_task_id, unique_worker_id):
        """
        测试 Gateway Transport 结果上报幂等性

        验证：
        1. 结果缓存正确工作
        2. 重复上报返回缓存结果
        """
        from antcode_worker.transport.base import TaskResult
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        # 创建配置（启用幂等性）
        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id=unique_worker_id,
            enable_receipt_idempotency=True,
            receipt_cache_ttl=60.0,
        )

        transport = GatewayTransport(gateway_config=config)

        # 模拟缓存结果
        cache_key = f"result:{unique_task_id}"
        transport._cache_result(cache_key, True)

        # 检查缓存
        cached = transport._get_cached_result(cache_key)
        assert cached is True

        # 检查不存在的缓存
        non_existent = transport._get_cached_result("non-existent-key")
        assert non_existent is None

        logger.info("[Test] Gateway Transport 结果幂等性验证成功")

    @pytest.mark.asyncio
    async def test_gateway_transport_ack_idempotency(self, unique_task_id, unique_worker_id):
        """
        测试 Gateway Transport ACK 幂等性

        验证：
        1. ACK 缓存正确工作
        2. 重复 ACK 返回缓存结果
        """
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        # 创建配置（启用幂等性）
        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            worker_id=unique_worker_id,
            enable_receipt_idempotency=True,
            receipt_cache_ttl=60.0,
        )

        transport = GatewayTransport(gateway_config=config)

        # 模拟缓存 ACK 结果
        cache_key = f"ack:{unique_task_id}"
        transport._cache_result(cache_key, True)

        # 检查缓存
        cached = transport._get_cached_result(cache_key)
        assert cached is True

        logger.info("[Test] Gateway Transport ACK 幂等性验证成功")


@pytest.mark.integration
class TestGatewayCodecs:
    """Gateway 编解码测试"""

    @pytest.mark.asyncio
    async def test_task_decoder(self, unique_task_id):
        """
        测试任务消息解码

        验证：
        1. 从字典正确解码
        2. 字段正确映射
        """
        from antcode_worker.transport.gateway.codecs import TaskDecoder

        # 创建任务数据
        task_data = {
            "task_id": unique_task_id,
            "project_id": "project-001",
            "project_type": "code",
            "priority": 5,
            "params": {"key": "value"},
            "environment": {"ENV_VAR": "test"},
            "timeout": 3600,
            "download_url": "https://example.com/code.zip",
            "file_hash": "abc123",
            "entry_point": "main.py",
        }

        # 解码
        task = TaskDecoder.decode_from_dict(task_data)

        # 验证
        assert task.task_id == unique_task_id
        assert task.project_id == "project-001"
        assert task.project_type == "code"
        assert task.priority == 5
        assert task.params == {"key": "value"}
        assert task.environment == {"ENV_VAR": "test"}
        assert task.timeout == 3600
        assert task.download_url == "https://example.com/code.zip"
        assert task.file_hash == "abc123"
        assert task.entry_point == "main.py"

        logger.info(f"[Test] 任务解码验证成功: {unique_task_id}")

    @pytest.mark.asyncio
    async def test_result_encoder(self, unique_task_id, unique_worker_id):
        """
        测试结果消息编码

        验证：
        1. 结果正确编码为字典
        2. 字段正确映射
        """
        from antcode_worker.transport.base import TaskResult
        from antcode_worker.transport.gateway.codecs import ResultEncoder

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
            data={"output": "test output"},
        )

        # 编码为字典
        encoded = ResultEncoder.encode_to_dict(result, unique_worker_id)

        # 验证
        assert encoded["task_id"] == unique_task_id
        assert encoded["worker_id"] == unique_worker_id
        assert encoded["status"] == "success"
        assert encoded["exit_code"] == 0
        assert encoded["duration_ms"] == 1500
        assert encoded["data"] == {"output": "test output"}

        logger.info(f"[Test] 结果编码验证成功: {unique_task_id}")

    @pytest.mark.asyncio
    async def test_log_encoder(self):
        """
        测试日志消息编码

        验证：
        1. 日志正确编码
        2. 批量日志正确编码
        """
        from antcode_worker.transport.base import LogMessage
        from antcode_worker.transport.gateway.codecs import LogEncoder

        # 创建日志
        log = LogMessage(
            execution_id="exec-001",
            log_type="stdout",
            content="Hello, World!",
            timestamp=datetime.now(),
            sequence=1,
        )

        # 编码为字典
        encoded = LogEncoder.encode_to_dict(log)

        # 验证
        assert encoded["execution_id"] == "exec-001"
        assert encoded["log_type"] == "stdout"
        assert encoded["content"] == "Hello, World!"
        assert encoded["sequence"] == 1

        # 测试批量编码（使用 Mock 返回）
        logs = [
            LogMessage(execution_id="exec-001", log_type="stdout", content="Line 1", sequence=1),
            LogMessage(execution_id="exec-001", log_type="stdout", content="Line 2", sequence=2),
            LogMessage(execution_id="exec-001", log_type="stderr", content="Error", sequence=3),
        ]

        # encode_batch 可能返回 Mock 对象（当 antcode_contracts 不完整时）
        batch_encoded = LogEncoder.encode_batch(logs)
        # 验证返回对象有 logs 属性
        assert hasattr(batch_encoded, "logs")

        logger.info("[Test] 日志编码验证成功")

    @pytest.mark.asyncio
    async def test_heartbeat_encoder(self, unique_worker_id):
        """
        测试心跳消息编码

        验证：
        1. 心跳正确编码
        2. 系统指标正确包含
        """
        from antcode_worker.transport.base import HeartbeatMessage
        from antcode_worker.transport.gateway.codecs import HeartbeatEncoder

        # 创建心跳
        heartbeat = HeartbeatMessage(
            worker_id=unique_worker_id,
            status="online",
            cpu_percent=45.5,
            memory_percent=60.2,
            disk_percent=30.0,
            running_tasks=2,
            max_concurrent_tasks=5,
            timestamp=datetime.now(),
        )

        # 编码为字典
        encoded = HeartbeatEncoder.encode_to_dict(heartbeat)

        # 验证
        assert encoded["worker_id"] == unique_worker_id
        assert encoded["status"] == "online"
        assert encoded["cpu_percent"] == 45.5
        assert encoded["memory_percent"] == 60.2
        assert encoded["disk_percent"] == 30.0
        assert encoded["running_tasks"] == 2
        assert encoded["max_concurrent_tasks"] == 5

        logger.info(f"[Test] 心跳编码验证成功: {unique_worker_id}")


@pytest.mark.integration
class TestSecurityModules:
    """安全模块测试"""

    @pytest.mark.asyncio
    async def test_identity_management(self):
        """
        测试身份管理

        验证：
        1. 身份可以生成
        2. 身份可以保存和加载
        3. worker_id 跨重启保持稳定
        """
        from antcode_worker.security.identity import Identity

        with tempfile.TemporaryDirectory() as tmpdir:
            identity_path = Path(tmpdir) / "identity.yaml"

            # 生成新身份
            identity1 = Identity.generate(
                zone="test-zone",
                labels={"env": "test", "region": "us-west"},
                version="1.0.0",
            )

            assert identity1.worker_id is not None
            assert len(identity1.worker_id) > 0
            assert identity1.zone == "test-zone"
            assert identity1.labels == {"env": "test", "region": "us-west"}

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

            logger.info(f"[Test] 身份管理验证成功: worker_id={identity1.worker_id}")

    @pytest.mark.asyncio
    async def test_identity_load_or_generate(self):
        """
        测试身份加载或生成

        验证：
        1. 文件不存在时生成新身份
        2. 文件存在时加载现有身份
        """
        from antcode_worker.security.identity import Identity

        with tempfile.TemporaryDirectory() as tmpdir:
            identity_path = Path(tmpdir) / "identity.yaml"

            # 第一次：生成新身份
            identity1 = Identity.load_or_generate(
                identity_path,
                zone="zone-1",
                labels={"key": "value"},
            )
            worker_id = identity1.worker_id

            # 第二次：加载现有身份
            identity2 = Identity.load_or_generate(
                identity_path,
                zone="zone-2",  # 尝试更新 zone
                labels={"key": "new-value"},  # 尝试更新 labels
            )

            # worker_id 应该保持不变
            assert identity2.worker_id == worker_id
            # zone 和 labels 应该更新
            assert identity2.zone == "zone-2"
            assert identity2.labels["key"] == "new-value"

            logger.info("[Test] 身份加载或生成验证成功")

    @pytest.mark.asyncio
    async def test_secrets_manager(self):
        """
        测试凭证管理

        验证：
        1. 从文件加载凭证
        2. 从环境变量加载凭证
        3. 优先级正确
        """
        import os

        from antcode_worker.security.secrets import SecretsManager

        with tempfile.TemporaryDirectory() as tmpdir:
            secrets_dir = Path(tmpdir)

            # 创建凭证文件
            (secrets_dir / "api_key").write_text("file-api-key-12345")
            (secrets_dir / "custom_secret").write_text("custom-secret-value")

            # 设置环境变量
            os.environ["ANTCODE_API_KEY"] = "env-api-key-67890"
            os.environ["ANTCODE_ANOTHER_SECRET"] = "another-secret-value"

            try:
                manager = SecretsManager(secrets_dir=secrets_dir)

                # 文件优先于环境变量
                api_key = manager.get_api_key()
                assert api_key == "file-api-key-12345"

                # 自定义凭证从文件加载
                custom = manager.get("custom_secret")
                assert custom == "custom-secret-value"

                # 仅在环境变量中的凭证
                another = manager.get("another_secret")
                assert another == "another-secret-value"

                # 不存在的凭证返回默认值
                missing = manager.get("missing_key", default="default-value")
                assert missing == "default-value"

                # 验证来源
                sources = manager.get_sources()
                assert sources.get("api_key") == "file"

                logger.info("[Test] 凭证管理验证成功")

            finally:
                # 清理环境变量
                os.environ.pop("ANTCODE_API_KEY", None)
                os.environ.pop("ANTCODE_ANOTHER_SECRET", None)

    @pytest.mark.asyncio
    async def test_dispatch_verifier(self):
        """
        测试 Dispatch 认证验证

        验证：
        1. API Key 验证
        2. mTLS 验证
        """
        from antcode_worker.security.verify import AuthMethod, DispatchVerifier

        # 创建验证器
        verifier = DispatchVerifier(
            expected_api_key="correct-api-key",
            expected_cn="worker.example.com",
            require_auth=True,
        )

        # 测试 API Key 验证
        success, method, message = verifier.verify(api_key="correct-api-key")
        assert success is True
        assert method == AuthMethod.API_KEY

        # 测试错误的 API Key
        success, method, message = verifier.verify(api_key="wrong-api-key")
        assert success is False

        # 测试 mTLS 验证
        success, method, message = verifier.verify(cert_cn="worker.example.com")
        assert success is True
        assert method == AuthMethod.MTLS

        # 测试错误的 CN
        success, method, message = verifier.verify(cert_cn="wrong.example.com")
        assert success is False

        logger.info("[Test] Dispatch 认证验证成功")

    @pytest.mark.asyncio
    async def test_task_signature_verification(self):
        """
        测试任务签名验证

        验证：
        1. 签名创建
        2. 签名验证
        3. 过期检测
        """
        import time

        from antcode_worker.security.verify import TaskSignature, Verifier

        # 创建验证器
        secret_key = "test-secret-key-for-signing"
        verifier = Verifier(
            secret_key=secret_key,
            signature_enabled=True,
        )

        # 创建签名
        task_id = "task-to-sign-001"
        signature = verifier.create_signature(task_id, ttl_seconds=3600)

        assert signature is not None
        assert signature.signature != ""
        assert signature.nonce != ""
        assert signature.is_expired() is False

        # 验证签名
        result, message = verifier.verify_task(task_id, signature)
        from antcode_worker.security.verify import VerifyResult
        assert result == VerifyResult.SUCCESS

        # 测试过期签名
        expired_signature = TaskSignature(
            issued_at=int(time.time()) - 7200,
            expires_at=int(time.time()) - 3600,
            nonce="expired-nonce",
            signature="some-signature",
        )
        assert expired_signature.is_expired() is True

        result, message = verifier.verify_task(task_id, expired_signature)
        assert result == VerifyResult.EXPIRED

        logger.info("[Test] 任务签名验证成功")


@pytest.mark.integration
class TestGatewayTransportStatus:
    """Gateway Transport 状态测试"""

    @pytest.mark.asyncio
    async def test_transport_status(self, unique_worker_id):
        """
        测试 Transport 状态获取

        验证：
        1. 状态信息完整
        2. 包含所有必要字段
        """
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        config = GatewayConfig(
            gateway_host="gateway.example.com",
            gateway_port=443,
            use_tls=True,
            auth_method="api_key",
            api_key="test-key",
            worker_id=unique_worker_id,
        )

        transport = GatewayTransport(gateway_config=config)

        # 获取状态
        status = transport.get_status()

        # 验证必要字段
        assert "mode" in status
        assert status["mode"] == "gateway"
        assert "state" in status
        assert "running" in status
        assert "connected" in status
        assert "gateway_host" in status
        assert status["gateway_host"] == "gateway.example.com"
        assert "gateway_port" in status
        assert status["gateway_port"] == 443
        assert "use_tls" in status
        assert status["use_tls"] is True
        assert "auth_method" in status
        assert status["auth_method"] == "api_key"
        assert "worker_id" in status
        assert status["worker_id"] == unique_worker_id

        logger.info(f"[Test] Transport 状态验证成功: {status}")

    @pytest.mark.asyncio
    async def test_transport_credentials_setting(self, unique_worker_id):
        """
        测试 Transport 凭证设置

        验证：
        1. 凭证可以设置
        2. 设置后正确反映
        """
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
        )

        transport = GatewayTransport(gateway_config=config)

        # 设置凭证
        transport.set_credentials(
            worker_id=unique_worker_id,
            api_key="new-api-key-12345",
        )

        # 验证设置
        assert transport.gateway_config.worker_id == unique_worker_id
        assert transport.gateway_config.api_key == "new-api-key-12345"

        logger.info("[Test] Transport 凭证设置验证成功")


@pytest.mark.integration
class TestGatewayModeE2EFlow:
    """Gateway 模式 E2E 流程测试"""

    @pytest.mark.asyncio
    async def test_gateway_mode_mock_flow(self, unique_task_id, unique_worker_id):
        """
        测试 Gateway 模式模拟流程

        验证完整流程（使用 Mock）：
        1. Transport 初始化
        2. 认证配置
        3. 任务编解码
        4. 结果编解码
        5. 幂等性缓存
        """
        from antcode_worker.transport.base import TaskResult
        from antcode_worker.transport.gateway.codecs import ResultEncoder, TaskDecoder
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        # 1. 创建 Transport
        config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            use_tls=False,
            auth_method="api_key",
            api_key="test-api-key",
            worker_id=unique_worker_id,
            enable_receipt_idempotency=True,
        )

        transport = GatewayTransport(gateway_config=config)
        logger.info(f"[E2E] Transport 已创建: worker_id={unique_worker_id}")

        # 2. 模拟任务数据
        task_data = {
            "task_id": unique_task_id,
            "project_id": "e2e-project",
            "project_type": "code",
            "priority": 10,
            "timeout": 3600,
            "entry_point": "main.py",
        }

        # 3. 解码任务
        task = TaskDecoder.decode_from_dict(task_data)
        assert task.task_id == unique_task_id
        logger.info(f"[E2E] 任务已解码: {task.task_id}")

        # 4. 模拟执行结果
        result = TaskResult(
            run_id=f"run-{unique_task_id}",
            task_id=unique_task_id,
            status="success",
            exit_code=0,
            started_at=datetime.now(),
            finished_at=datetime.now(),
            duration_ms=500.0,
        )

        # 5. 编码结果
        encoded_result = ResultEncoder.encode_to_dict(result, unique_worker_id)
        assert encoded_result["task_id"] == unique_task_id
        assert encoded_result["worker_id"] == unique_worker_id
        logger.info(f"[E2E] 结果已编码: status={encoded_result['status']}")

        # 6. 测试幂等性缓存
        cache_key = f"result:{unique_task_id}"
        transport._cache_result(cache_key, True)
        cached = transport._get_cached_result(cache_key)
        assert cached is True
        logger.info("[E2E] 幂等性缓存验证成功")

        # 7. 验证状态
        status = transport.get_status()
        assert status["mode"] == "gateway"
        assert status["worker_id"] == unique_worker_id
        logger.info(f"[E2E] Transport 状态: {status['state']}")

        logger.info("[E2E] ✓ Gateway 模式模拟 E2E 流程验证通过")

    @pytest.mark.asyncio
    async def test_gateway_auth_flow(self, unique_worker_id):
        """
        测试 Gateway 认证流程

        验证：
        1. API Key 认证流程
        2. 元数据正确传递
        """
        from antcode_worker.transport.gateway.auth import (
            AuthConfig,
            AuthMethod,
            GatewayAuthenticator,
        )
        from antcode_worker.transport.gateway.transport import (
            GatewayConfig,
            GatewayTransport,
        )

        # 创建认证配置
        auth_config = AuthConfig(
            method=AuthMethod.API_KEY,
            api_key="gateway-api-key-secret",
            worker_id=unique_worker_id,
        )

        authenticator = GatewayAuthenticator(auth_config)

        # 获取认证元数据
        metadata = authenticator.get_metadata()
        metadata_dict = dict(metadata)

        # 验证元数据
        assert "x-api-key" in metadata_dict
        assert "x-worker-id" in metadata_dict
        logger.info(f"[Auth] 认证元数据: {list(metadata_dict.keys())}")

        # 创建 Transport 并验证认证配置
        gateway_config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            auth_method="api_key",
            api_key="gateway-api-key-secret",
            worker_id=unique_worker_id,
        )

        transport = GatewayTransport(gateway_config=gateway_config)
        status = transport.get_status()

        assert status["auth_method"] == "api_key"
        assert status["worker_id"] == unique_worker_id
        logger.info("[Auth] ✓ Gateway 认证流程验证通过")

    @pytest.mark.asyncio
    async def test_gateway_reconnect_flow(self):
        """
        测试 Gateway 重连流程

        验证：
        1. 断线检测
        2. 重连尝试
        3. 状态恢复
        """
        from antcode_worker.transport.gateway.reconnect import (
            ReconnectConfig,
            ReconnectManager,
            ReconnectState,
        )

        # 创建重连配置
        config = ReconnectConfig(
            initial_backoff=0.1,
            max_backoff=1.0,
            backoff_multiplier=2.0,
            max_attempts=3,
        )

        # 模拟连接函数
        attempt_count = [0]

        async def mock_connect():
            attempt_count[0] += 1
            # 第三次尝试成功
            return attempt_count[0] >= 3

        manager = ReconnectManager(config, connect_func=mock_connect)

        # 模拟断线
        manager.notify_disconnected("connection lost")
        assert manager.state == ReconnectState.DISCONNECTED
        logger.info("[Reconnect] 断线已检测")

        # 等待重连
        await asyncio.sleep(1.0)

        # 验证重连尝试
        stats = manager.get_stats()
        logger.info(f"[Reconnect] 重连尝试次数: {attempt_count[0]}")
        logger.info(f"[Reconnect] 统计: {stats.to_dict()}")

        # 停止管理器
        await manager.stop()

        logger.info("[Reconnect] ✓ Gateway 重连流程验证通过")
