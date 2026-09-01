"""Worker 启动冒烟：CLI/配置/传输层/各模块可导入，流量控制基本行为正确。"""

import pytest


class TestWorkerStartup:
    def test_cli_import(self):
        from antcode_worker.cli import main, start_worker

        assert callable(main)
        assert callable(start_worker)

    def test_config_import(self):
        from antcode_worker.config import init_worker_config

        assert callable(init_worker_config)

    def test_worker_config_initialization(self):
        from antcode_worker.config import init_worker_config

        config = init_worker_config(
            name="Test-Worker",
            port=8001,
            region="test",
            transport_mode="direct",
        )

        assert config.name == "Test-Worker"
        assert config.port == 8001
        assert config.region == "test"
        assert config.transport_mode == "direct"
        assert config.max_concurrent_tasks > 0

    def test_dependency_container_initialization_state(self):
        """容器完成依赖装配后必须可显式标记为已初始化。"""
        from antcode_worker.app.wiring import Container

        container = Container()

        assert container.is_initialized() is False
        container.mark_initialized()
        assert container.is_initialized() is True


class TestTransportLayer:
    def test_transport_base_import(self):
        from antcode_worker.transport.base import TransportMode, WorkerState

        assert TransportMode.DIRECT.value == "direct"
        assert TransportMode.GATEWAY.value == "gateway"
        assert WorkerState.ONLINE.value == "online"

    def test_redis_transport_import(self):
        from antcode_worker.transport.redis.transport import RedisTransport

        transport = RedisTransport(redis_url="redis://localhost:6379/0")
        assert transport.mode.value == "direct"
        assert not transport.is_running

    def test_gateway_transport_import(self):
        from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport

        gateway_config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
        )
        transport = GatewayTransport(gateway_config=gateway_config)
        assert transport.mode.value == "gateway"
        assert not transport.is_running


class TestModuleImports:
    def test_runtime_module(self):
        from antcode_worker.runtime.cache_gc import GCConfig
        from antcode_worker.runtime.uv_manager import UVManager

        uv_manager = UVManager()
        assert uv_manager is not None

        gc_config = GCConfig()
        assert gc_config.env_ttl > 0

    def test_executor_module(self):
        from antcode_worker.domain.enums import RunStatus

        assert RunStatus.PENDING.value == "pending"
        assert RunStatus.RUNNING.value == "running"
        assert RunStatus.SUCCESS.value == "success"
        assert RunStatus.FAILED.value == "failed"

    def test_logs_module(self):
        from antcode_worker.logs import LogManager, LogStreamer

        assert LogStreamer is not None
        assert LogManager is not None

    def test_heartbeat_module(self):
        from antcode_worker.heartbeat.capability_detector import CapabilityDetector

        detector = CapabilityDetector()
        capabilities = detector.detect_all()
        assert isinstance(capabilities, dict)


class TestFlowControl:
    def test_flow_control_import(self):
        from antcode_worker.transport.flow_control import FlowControlStrategy

        assert FlowControlStrategy.TOKEN_BUCKET.value == "token_bucket"
        assert FlowControlStrategy.AIMD.value == "aimd"
        assert FlowControlStrategy.SLIDING_WINDOW.value == "sliding_window"

    def test_backpressure_levels(self):
        from antcode_worker.transport.flow_control import BackpressureLevel

        assert BackpressureLevel.NONE.value == "none"
        assert BackpressureLevel.LOW.value == "low"
        assert BackpressureLevel.MEDIUM.value == "medium"
        assert BackpressureLevel.HIGH.value == "high"
        assert BackpressureLevel.CRITICAL.value == "critical"

    def test_flow_control_config(self):
        from antcode_worker.transport.flow_control import FlowControlConfig, FlowControlStrategy

        config = FlowControlConfig(
            strategy=FlowControlStrategy.TOKEN_BUCKET,
            bucket_capacity=50,
            refill_rate=10.0,
        )

        assert config.strategy == FlowControlStrategy.TOKEN_BUCKET
        assert config.bucket_capacity == 50
        assert config.refill_rate == 10.0

    def test_create_flow_controller_factory(self):
        from antcode_worker.transport.flow_control import (
            AIMDController,
            FlowControlStrategy,
            SlidingWindowController,
            TokenBucketController,
            create_flow_controller,
        )

        tb = create_flow_controller(FlowControlStrategy.TOKEN_BUCKET)
        assert isinstance(tb, TokenBucketController)

        aimd = create_flow_controller(FlowControlStrategy.AIMD)
        assert isinstance(aimd, AIMDController)

        sw = create_flow_controller(FlowControlStrategy.SLIDING_WINDOW)
        assert isinstance(sw, SlidingWindowController)

    def test_backpressure_manager(self):
        from antcode_worker.transport.flow_control import (
            BackpressureLevel,
            BackpressureManager,
            FlowControlConfig,
            TokenBucketController,
        )

        manager = BackpressureManager()
        config = FlowControlConfig(bucket_capacity=10, refill_rate=5.0)
        controller = TokenBucketController(config)

        manager.register("test", controller)
        assert manager.get_level() == BackpressureLevel.NONE
        assert not manager.should_pause()
        assert manager.get_delay_factor() == 1.0

        manager.unregister("test")
        assert manager.get_level() == BackpressureLevel.NONE


@pytest.mark.asyncio
class TestFlowControlAsync:
    async def test_token_bucket_acquire(self):
        from antcode_worker.transport.flow_control import FlowControlConfig, TokenBucketController

        config = FlowControlConfig(bucket_capacity=3, refill_rate=1.0)
        controller = TokenBucketController(config)

        # 应该允许前 3 个请求
        for _ in range(3):
            result = await controller.acquire()
            assert result

        # 第 4 个请求应该被拒绝（无超时）
        result = await controller.acquire(timeout=0)
        assert not result

        # 验证统计
        assert controller.stats.total_requests == 4
        assert controller.stats.allowed_requests == 3
        assert controller.stats.rejected_requests == 1

    async def test_aimd_rate_adjustment(self):
        from antcode_worker.transport.flow_control import AIMDController, FlowControlConfig, FlowControlStrategy

        config = FlowControlConfig(
            strategy=FlowControlStrategy.AIMD,
            initial_rate=10.0,
            additive_increase=2.0,
            multiplicative_decrease=0.5,
        )
        controller = AIMDController(config)

        initial_rate = controller._current_rate

        # 10 次成功后速率应该增加
        for _ in range(10):
            controller.on_success()
        assert controller._current_rate > initial_rate

        # 失败后速率应该降低
        rate_before_failure = controller._current_rate
        controller.on_failure()
        assert controller._current_rate < rate_before_failure

    async def test_sliding_window_limit(self):
        from antcode_worker.transport.flow_control import (
            FlowControlConfig,
            FlowControlStrategy,
            SlidingWindowController,
        )

        config = FlowControlConfig(
            strategy=FlowControlStrategy.SLIDING_WINDOW,
            window_size=1.0,
            max_requests_per_window=3,
        )
        controller = SlidingWindowController(config)

        # 应该允许前 3 个请求
        for _ in range(3):
            result = await controller.acquire()
            assert result

        # 第 4 个请求应该被拒绝
        result = await controller.acquire(timeout=0)
        assert not result


@pytest.mark.asyncio
class TestTransportAsync:
    async def test_redis_transport_start_stop(self):
        from antcode_worker.transport.redis.transport import RedisTransport

        transport = RedisTransport(redis_url="redis://localhost:6379/0")

        # 启动（预期失败，因为 Redis 未运行）
        await transport.start()
        # 不检查结果，因为 Redis 可能未运行

        # 停止
        await transport.stop()
        assert not transport.is_running

    async def test_gateway_transport_start_stop(self):
        from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport

        gateway_config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
            # 预期连不上：缩短 channel_ready 等待，避免默认 10s 连接超时拖慢单测
            connect_timeout=0.1,
        )
        transport = GatewayTransport(gateway_config=gateway_config)

        # 启动（预期失败，因为 Gateway 未运行）
        await transport.start()
        # 不检查结果，因为 Gateway 可能未运行

        # 停止
        await transport.stop()
        assert not transport.is_running

    async def test_transport_status(self):
        from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport
        from antcode_worker.transport.redis.transport import RedisTransport

        redis_transport = RedisTransport(redis_url="redis://localhost:6379/0")
        status = redis_transport.get_status()
        assert "mode" in status
        assert status["mode"] == "direct"

        gateway_config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
        )
        gateway_transport = GatewayTransport(gateway_config=gateway_config)
        status = gateway_transport.get_status()
        assert "mode" in status
        assert status["mode"] == "gateway"
