"""
Worker 服务启动验证测试

验证 Worker 服务的基本功能：
- CLI 导入
- 配置初始化
- 传输层初始化
- 模块导入

Requirements: 7.1, 7.2
"""

import pytest


class TestWorkerStartup:
    """Worker 启动测试"""

    def test_cli_import(self):
        """测试 CLI 模块导入"""
        from antcode_worker.cli import main, start_worker
        assert callable(main)
        assert callable(start_worker)

    def test_config_import(self):
        """测试配置模块导入"""
        from antcode_worker.config import (
            WorkerConfig,
            init_worker_config,
        )
        assert callable(init_worker_config)

    def test_worker_config_initialization(self):
        """测试 Worker 配置初始化"""
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


class TestTransportLayer:
    """传输层测试"""

    def test_transport_base_import(self):
        """测试传输层基类导入"""
        from antcode_worker.transport import (
            TransportBase,
            TransportMode,
            WorkerState,
            ServerConfig,
        )
        
        assert TransportMode.DIRECT.value == "direct"
        assert TransportMode.GATEWAY.value == "gateway"
        assert WorkerState.ONLINE.value == "online"

    def test_redis_transport_import(self):
        """测试 Redis 传输层导入"""
        from antcode_worker.transport import RedisTransport
        
        transport = RedisTransport(redis_url="redis://localhost:6379/0")
        assert transport.mode.value == "direct"
        assert not transport.is_running

    def test_gateway_transport_import(self):
        """测试 Gateway 传输层导入"""
        from antcode_worker.transport import GatewayTransport
        from antcode_worker.transport.gateway.transport import GatewayConfig
        
        gateway_config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
        )
        transport = GatewayTransport(gateway_config=gateway_config)
        assert transport.mode.value == "gateway"
        assert not transport.is_running


class TestModuleImports:
    """模块导入测试"""

    def test_runtime_module(self):
        """测试运行时模块导入"""
        from antcode_worker.runtime import UVManager, CacheGC, GCConfig
        
        uv_manager = UVManager()
        assert uv_manager is not None
        
        gc_config = GCConfig()
        assert gc_config.env_ttl > 0

    def test_executor_module(self):
        """测试执行器模块导入"""
        from antcode_worker.executor import (
            BaseExecutor,
            ExecutorConfig,
            ProcessExecutor,
            SandboxExecutor,
            ArtifactCollector,
        )
        from antcode_worker.domain.enums import RunStatus
        
        assert RunStatus.PENDING.value == "pending"
        assert RunStatus.RUNNING.value == "running"
        assert RunStatus.SUCCESS.value == "success"
        assert RunStatus.FAILED.value == "failed"

    def test_logging_module(self):
        """测试日志模块导入"""
        from antcode_worker.logging import (
            LogStreamer,
            BufferedLogStreamer,
            LogArchiver,
            ArchiverConfig,
        )
        
        config = ArchiverConfig()
        assert config.chunk_size > 0

    def test_heartbeat_module(self):
        """测试心跳模块导入"""
        from antcode_worker.heartbeat import (
            HeartbeatReporter,
            CapabilityDetector,
            Heartbeat,
            Metrics,
            OSInfo,
        )
        
        detector = CapabilityDetector()
        capabilities = detector.detect_all()
        assert isinstance(capabilities, dict)


class TestFlowControl:
    """流量控制测试"""

    def test_flow_control_import(self):
        """测试流量控制模块导入"""
        from antcode_worker.transport import (
            FlowController,
            FlowControlConfig,
            FlowControlStats,
            FlowControlStrategy,
            BackpressureLevel,
            BackpressureManager,
            TokenBucketController,
            AIMDController,
            SlidingWindowController,
            create_flow_controller,
        )

        assert FlowControlStrategy.TOKEN_BUCKET.value == "token_bucket"
        assert FlowControlStrategy.AIMD.value == "aimd"
        assert FlowControlStrategy.SLIDING_WINDOW.value == "sliding_window"

    def test_backpressure_levels(self):
        """测试背压级别"""
        from antcode_worker.transport import BackpressureLevel

        assert BackpressureLevel.NONE.value == "none"
        assert BackpressureLevel.LOW.value == "low"
        assert BackpressureLevel.MEDIUM.value == "medium"
        assert BackpressureLevel.HIGH.value == "high"
        assert BackpressureLevel.CRITICAL.value == "critical"

    def test_flow_control_config(self):
        """测试流量控制配置"""
        from antcode_worker.transport import FlowControlConfig, FlowControlStrategy

        config = FlowControlConfig(
            strategy=FlowControlStrategy.TOKEN_BUCKET,
            bucket_capacity=50,
            refill_rate=10.0,
        )

        assert config.strategy == FlowControlStrategy.TOKEN_BUCKET
        assert config.bucket_capacity == 50
        assert config.refill_rate == 10.0

    def test_create_flow_controller_factory(self):
        """测试流量控制器工厂函数"""
        from antcode_worker.transport import (
            FlowControlStrategy,
            TokenBucketController,
            AIMDController,
            SlidingWindowController,
            create_flow_controller,
        )

        tb = create_flow_controller(FlowControlStrategy.TOKEN_BUCKET)
        assert isinstance(tb, TokenBucketController)

        aimd = create_flow_controller(FlowControlStrategy.AIMD)
        assert isinstance(aimd, AIMDController)

        sw = create_flow_controller(FlowControlStrategy.SLIDING_WINDOW)
        assert isinstance(sw, SlidingWindowController)

    def test_backpressure_manager(self):
        """测试背压管理器"""
        from antcode_worker.transport import (
            BackpressureManager,
            BackpressureLevel,
            TokenBucketController,
            FlowControlConfig,
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
    """流量控制异步测试"""

    async def test_token_bucket_acquire(self):
        """测试令牌桶获取许可"""
        from antcode_worker.transport import TokenBucketController, FlowControlConfig

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
        """测试 AIMD 速率调整"""
        from antcode_worker.transport import AIMDController, FlowControlConfig, FlowControlStrategy

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
        """测试滑动窗口限制"""
        from antcode_worker.transport import SlidingWindowController, FlowControlConfig, FlowControlStrategy

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
    """传输层异步测试"""

    async def test_redis_transport_start_stop(self):
        """测试 Redis 传输层启动和停止"""
        from antcode_worker.transport import RedisTransport
        
        transport = RedisTransport(redis_url="redis://localhost:6379/0")
        
        # 启动（预期失败，因为 Redis 未运行）
        result = await transport.start()
        # 不检查结果，因为 Redis 可能未运行
        
        # 停止
        await transport.stop()
        assert not transport.is_running

    async def test_gateway_transport_start_stop(self):
        """测试 Gateway 传输层启动和停止"""
        from antcode_worker.transport import GatewayTransport
        from antcode_worker.transport.gateway.transport import GatewayConfig
        
        gateway_config = GatewayConfig(
            gateway_host="localhost",
            gateway_port=50051,
        )
        transport = GatewayTransport(gateway_config=gateway_config)
        
        # 启动（预期失败，因为 Gateway 未运行）
        result = await transport.start()
        # 不检查结果，因为 Gateway 可能未运行
        
        # 停止
        await transport.stop()
        assert not transport.is_running

    async def test_transport_status(self):
        """测试传输层状态获取"""
        from antcode_worker.transport import RedisTransport, GatewayTransport
        from antcode_worker.transport.gateway.transport import GatewayConfig
        
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
