"""
GatewayService 测试

测试 Register 和 WorkerStream 功能。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from antcode_gateway.services.gateway_service import GatewayServiceImpl


class TestGatewayServiceRegister:
    """Register 接口测试"""

    @pytest.fixture
    def service(self):
        return GatewayServiceImpl()

    @pytest.mark.asyncio
    async def test_register_missing_api_key(self, service):
        """缺少 API Key 时注册失败"""
        request = MagicMock()
        request.worker_id = "worker-001"
        request.api_key = ""
        context = MagicMock()

        response = await service.Register(request, context)

        assert response.success is False
        assert "API Key" in response.error

    @pytest.mark.asyncio
    async def test_register_valid_api_key_format(self, service):
        """有效 API Key 格式通过验证（fallback 模式）"""
        request = MagicMock()
        request.worker_id = "worker-001"
        request.api_key = "ak_1234567890abcdef1234567890abcdef"
        request.HasField = MagicMock(return_value=False)
        context = MagicMock()

        # Mock Worker 模型不可用
        with patch.dict("sys.modules", {"antcode_core.domain.models": None}):
            response = await service.Register(request, context)

        assert response.success is True
        assert response.worker_id == "worker-001"

    @pytest.mark.asyncio
    async def test_register_invalid_api_key_format(self, service):
        """无效 API Key 格式被拒绝"""
        request = MagicMock()
        request.worker_id = "worker-001"
        request.api_key = "invalid"
        context = MagicMock()

        # Mock Worker 模型不可用
        with patch.dict("sys.modules", {"antcode_core.domain.models": None}):
            response = await service.Register(request, context)

        assert response.success is False

    @pytest.mark.asyncio
    async def test_verify_registration_with_worker(self, service):
        """验证注册 - Worker 存在"""
        mock_worker = MagicMock()
        mock_worker.public_id = "worker-001"
        mock_worker.name = "Test Worker"
        mock_worker.save = AsyncMock()

        with patch(
            "antcode_gateway.services.gateway_service.GatewayServiceImpl._verify_registration"
        ) as mock_verify:
            mock_verify.return_value = (True, "", mock_worker)

            request = MagicMock()
            request.worker_id = "worker-001"
            request.api_key = "ak_valid_key"
            request.HasField = MagicMock(return_value=False)
            context = MagicMock()

            response = await service.Register(request, context)

            assert response.success is True


class TestGatewayServiceWorkerStream:
    """WorkerStream 测试"""

    @pytest.fixture
    def service(self):
        return GatewayServiceImpl()

    def test_service_initialization(self, service):
        """服务初始化"""
        assert service._active_streams == {}
        assert service._stream_tasks == {}
        assert service.heartbeat_handler is not None
        assert service.result_handler is not None
        assert service.log_handler is not None
        assert service.poll_handler is not None

    @pytest.mark.asyncio
    async def test_control_poller_redis_unavailable(self, service):
        """控制轮询器 - Redis 不可用"""
        import asyncio

        response_queue = asyncio.Queue()
        stop_event = asyncio.Event()

        # Mock Redis 不可用
        with patch.object(service, "_get_redis_client", return_value=None):
            # 立即停止
            stop_event.set()
            await service._control_poller("worker-001", response_queue, stop_event)

        # 队列应该为空
        assert response_queue.empty()


class TestVerifyRegistration:
    """_verify_registration 方法测试"""

    @pytest.fixture
    def service(self):
        return GatewayServiceImpl()

    @pytest.mark.asyncio
    async def test_verify_with_valid_worker(self, service):
        """验证有效的 Worker"""
        mock_worker = MagicMock()
        mock_worker.public_id = "worker-001"
        mock_worker.name = "Test Worker"
        mock_worker.save = AsyncMock()

        mock_filter = MagicMock()
        mock_filter.first = AsyncMock(return_value=mock_worker)

        with patch("antcode_core.domain.models.Worker") as MockWorker:
            MockWorker.filter.return_value = mock_filter

            is_valid, error, worker = await service._verify_registration(
                api_key="ak_test_key",
                worker_id="worker-001",
            )

            assert is_valid is True
            assert error == ""
            assert worker == mock_worker

    @pytest.mark.asyncio
    async def test_verify_worker_id_mismatch(self, service):
        """验证节点 ID 不匹配"""
        mock_worker = MagicMock()
        mock_worker.public_id = "worker-002"
        mock_worker.name = "Test Worker"

        mock_filter = MagicMock()
        mock_filter.first = AsyncMock(return_value=mock_worker)

        with patch("antcode_core.domain.models.Worker") as MockWorker:
            MockWorker.filter.return_value = mock_filter

            is_valid, error, worker = await service._verify_registration(
                api_key="ak_test_key",
                worker_id="worker-001",
            )

            assert is_valid is False
            assert "Worker ID 不匹配" in error

    @pytest.mark.asyncio
    async def test_verify_api_key_not_found(self, service):
        """验证 API Key 不存在"""
        mock_filter = MagicMock()
        mock_filter.first = AsyncMock(return_value=None)

        with patch("antcode_core.domain.models.Worker") as MockWorker:
            MockWorker.filter.return_value = mock_filter

            # 无效格式
            is_valid, error, worker = await service._verify_registration(
                api_key="invalid",
                worker_id="worker-001",
            )

            assert is_valid is False
            assert "无效" in error

    @pytest.mark.asyncio
    async def test_verify_fallback_valid_format(self, service):
        """验证 fallback - 有效格式"""
        mock_filter = MagicMock()
        mock_filter.first = AsyncMock(return_value=None)

        with patch("antcode_core.domain.models.Worker") as MockWorker:
            MockWorker.filter.return_value = mock_filter

            # 有效格式但不在数据库
            is_valid, error, worker = await service._verify_registration(
                api_key="ak_1234567890abcdef",
                worker_id="worker-001",
            )

            # Fallback 模式应该通过
            assert is_valid is True
            assert worker is None
