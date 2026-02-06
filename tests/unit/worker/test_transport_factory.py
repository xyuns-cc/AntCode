"""
传输层工厂测试

测试配置校验、Banner 打印等功能。
"""

import pytest
from unittest.mock import patch, MagicMock

from antcode_worker.transport.factory import (
    TransportConfig,
    TransportConfigError,
    DirectConfig,
    GatewayConfigSpec,
    validate_transport_config,
    build_transport_config_from_env,
)


class TestTransportConfigValidation:
    """配置校验测试"""

    def test_invalid_mode_raises_error(self):
        """无效的 mode 应该抛出错误"""
        config = TransportConfig(
            mode="invalid",
            worker_id="worker-001",
        )

        with pytest.raises(TransportConfigError) as exc_info:
            validate_transport_config(config)

        assert "无效的 transport.mode" in str(exc_info.value)

    def test_direct_mode_requires_redis_url(self):
        """Direct 模式必须配置 redis_url"""
        config = TransportConfig(
            mode="direct",
            worker_id="worker-001",
            direct=DirectConfig(redis_url=""),
        )

        with pytest.raises(TransportConfigError) as exc_info:
            validate_transport_config(config)

        assert "redis_url" in str(exc_info.value)

    def test_direct_mode_forbids_gateway_config(self):
        """Direct 模式禁止配置 gateway"""
        config = TransportConfig(
            mode="direct",
            worker_id="worker-001",
            direct=DirectConfig(redis_url="redis://10.0.0.10:6379/0"),
            gateway=GatewayConfigSpec(host="gateway.example.com", port=50051),
        )

        with pytest.raises(TransportConfigError) as exc_info:
            validate_transport_config(config)

        assert "禁止配置 gateway" in str(exc_info.value)

    def test_gateway_mode_requires_host(self):
        """Gateway 模式必须配置 host"""
        config = TransportConfig(
            mode="gateway",
            worker_id="worker-001",
            gateway=GatewayConfigSpec(host="", endpoint=""),
        )

        with pytest.raises(TransportConfigError) as exc_info:
            validate_transport_config(config)

        assert "gateway.host" in str(exc_info.value)

    def test_gateway_mode_forbids_redis_url(self):
        """Gateway 模式禁止配置 redis_url（非 localhost）"""
        config = TransportConfig(
            mode="gateway",
            worker_id="worker-001",
            direct=DirectConfig(redis_url="redis://10.0.0.10:6379/0"),
            gateway=GatewayConfigSpec(host="gateway.example.com"),
        )

        with pytest.raises(TransportConfigError) as exc_info:
            validate_transport_config(config)

        assert "禁止配置 redis_url" in str(exc_info.value)

    def test_worker_id_required(self):
        """必须配置 worker_id"""
        config = TransportConfig(
            mode="gateway",
            worker_id=None,
            gateway=GatewayConfigSpec(host="gateway.example.com"),
        )

        with pytest.raises(TransportConfigError) as exc_info:
            validate_transport_config(config)

        assert "worker_id" in str(exc_info.value)

    def test_valid_direct_config(self):
        """有效的 Direct 配置应该通过校验"""
        config = TransportConfig(
            mode="direct",
            worker_id="worker-001",
            direct=DirectConfig(redis_url="redis://10.0.0.10:6379/0"),
            gateway=GatewayConfigSpec(),  # 默认值
        )

        # 不应抛出异常
        validate_transport_config(config)

    def test_valid_gateway_config(self):
        """有效的 Gateway 配置应该通过校验"""
        config = TransportConfig(
            mode="gateway",
            worker_id="worker-001",
            direct=DirectConfig(),  # 默认值
            gateway=GatewayConfigSpec(host="gateway.example.com", port=50051),
        )

        # 不应抛出异常
        validate_transport_config(config)

    def test_gateway_with_localhost_redis_allowed(self):
        """Gateway 模式允许 localhost redis（开发环境）"""
        config = TransportConfig(
            mode="gateway",
            worker_id="worker-001",
            direct=DirectConfig(redis_url="redis://localhost:6379/0"),
            gateway=GatewayConfigSpec(host="gateway.example.com"),
        )

        # 不应抛出异常
        validate_transport_config(config)


class TestBuildTransportConfigFromEnv:
    """从环境变量构建配置测试"""

    def test_build_from_env_direct_mode(self):
        """从环境变量构建 Direct 模式配置"""
        with patch.dict("os.environ", {
            "WORKER_TRANSPORT_MODE": "direct",
            "WORKER_ID": "worker-001",
            "WORKER_REDIS_URL": "redis://10.0.0.10:6379/0",
            "WORKER_CONSUMER_GROUP": "my-workers",
        }):
            config = build_transport_config_from_env()

            assert config.mode == "direct"
            assert config.worker_id == "worker-001"
            assert config.direct.redis_url == "redis://10.0.0.10:6379/0"
            assert config.direct.consumer_group == "my-workers"

    def test_build_from_env_gateway_mode(self):
        """从环境变量构建 Gateway 模式配置"""
        with patch.dict("os.environ", {
            "WORKER_TRANSPORT_MODE": "gateway",
            "WORKER_ID": "worker-002",
            "WORKER_GATEWAY_HOST": "gateway.example.com",
            "WORKER_GATEWAY_PORT": "50052",
            "WORKER_GATEWAY_TLS": "true",
            "WORKER_API_KEY": "ak_test_key",
        }):
            config = build_transport_config_from_env()

            assert config.mode == "gateway"
            assert config.worker_id == "worker-002"
            assert config.gateway.host == "gateway.example.com"
            assert config.gateway.port == 50052
            assert config.gateway.tls is True
            assert config.gateway.api_key == "ak_test_key"

    def test_parameter_overrides_env(self):
        """参数优先于环境变量"""
        with patch.dict("os.environ", {
            "WORKER_TRANSPORT_MODE": "gateway",
            "WORKER_ID": "env-worker",
        }):
            config = build_transport_config_from_env(
                transport_mode="direct",
                worker_id="param-worker",
                redis_url="redis://param-host:6379/0",
            )

            assert config.mode == "direct"
            assert config.worker_id == "param-worker"
            assert config.direct.redis_url == "redis://param-host:6379/0"

    def test_default_values(self):
        """默认值测试"""
        with patch.dict("os.environ", {}, clear=True):
            config = build_transport_config_from_env()

            assert config.mode == "gateway"
            assert config.worker_id is None
            assert config.gateway.host == "localhost"
            assert config.gateway.port == 50051
            assert config.gateway.tls is False


class TestTransportConfigMutualExclusion:
    """配置互斥测试"""

    def test_direct_mode_with_gateway_endpoint_fails(self):
        """Direct 模式配置了 gateway endpoint 应该失败"""
        config = TransportConfig(
            mode="direct",
            worker_id="worker-001",
            direct=DirectConfig(redis_url="redis://10.0.0.10:6379/0"),
            gateway=GatewayConfigSpec(endpoint="gateway.example.com:50051"),
        )

        with pytest.raises(TransportConfigError):
            validate_transport_config(config)

    def test_gateway_mode_with_external_redis_fails(self):
        """Gateway 模式配置了外部 Redis 应该失败"""
        config = TransportConfig(
            mode="gateway",
            worker_id="worker-001",
            direct=DirectConfig(redis_url="redis://production-redis:6379/0"),
            gateway=GatewayConfigSpec(host="gateway.example.com"),
        )

        with pytest.raises(TransportConfigError):
            validate_transport_config(config)
