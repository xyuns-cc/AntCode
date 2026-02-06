"""节点项目同步服务单元测试

测试新架构中的项目同步逻辑，验证 S3 预签名 URL 的使用。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestWorkerProjectSyncService:
    """测试 WorkerProjectSyncService"""

    @pytest.fixture
    def mock_project(self):
        """模拟项目对象"""
        project = MagicMock()
        project.id = 1
        project.public_id = "proj-001"
        project.name = "Test Project"
        project.type = MagicMock(value="file")
        return project

    @pytest.fixture
    def mock_worker(self):
        """模拟 Worker 对象"""
        worker = MagicMock()
        worker.id = 1
        worker.public_id = "worker-001"
        worker.name = "Test Worker"
        worker.api_key = "test-api-key"
        return worker

    @pytest.mark.asyncio
    async def test_sync_requires_s3_storage(self, mock_worker):
        """测试同步需要 S3 存储"""
        with patch(
            "antcode_core.infrastructure.storage.presign.is_s3_storage_enabled",
            return_value=False,
        ):
            from antcode_core.application.services.workers.worker_project_sync import (
                WorkerProjectSyncService,
            )

            service = WorkerProjectSyncService()
            results, info = await service.sync_projects_to_worker_with_info(
                mock_worker, ["proj-001", "proj-002"]
            )

            assert results["synced"] == []
            assert results["skipped"] == []
            assert len(results["failed"]) == 2
            assert all("S3 存储未配置" in f["reason"] for f in results["failed"])

    @pytest.mark.asyncio
    async def test_sync_project_not_found(self, mock_worker):
        """测试项目不存在"""
        with patch(
            "antcode_core.infrastructure.storage.presign.is_s3_storage_enabled",
            return_value=True,
        ), patch(
            "antcode_core.domain.models.Project.get_or_none",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from antcode_core.application.services.workers.worker_project_sync import (
                WorkerProjectSyncService,
            )

            service = WorkerProjectSyncService()
            results, info = await service.sync_projects_to_worker_with_info(
                mock_worker, ["nonexistent"]
            )

            assert "nonexistent" not in results["synced"]
            assert len(results["failed"]) == 1
            assert results["failed"][0]["reason"] == "项目不存在"

    @pytest.mark.asyncio
    async def test_sync_s3_project_with_presigned_url(self, mock_worker, mock_project):
        """测试 S3 项目使用预签名 URL"""
        presigned_url = "https://s3.example.com/bucket/file.zip?signature=xxx"

        mock_sync_service = MagicMock()
        mock_sync_service.get_project_transfer_info = AsyncMock(
            return_value={
                "transfer_method": "s3_original",
                "file_path": "projects/proj-001/file.zip",
                "file_hash": "abc123",
                "entry_point": "main.py",
                "presigned_url": presigned_url,
            }
        )

        with patch(
            "antcode_core.infrastructure.storage.presign.is_s3_storage_enabled",
            return_value=True,
        ), patch(
            "antcode_core.domain.models.Project.get_or_none",
            new_callable=AsyncMock,
            return_value=mock_project,
        ), patch(
            "antcode_core.application.services.projects.project_sync_service.project_sync_service",
            mock_sync_service,
        ):
            from antcode_core.application.services.workers.worker_project_sync import (
                WorkerProjectSyncService,
            )

            service = WorkerProjectSyncService()
            results, info = await service.sync_projects_to_worker_with_info(
                mock_worker, ["proj-001"]
            )

            assert "proj-001" in results["synced"]
            assert "proj-001" in info
            assert info["proj-001"]["download_url"] == presigned_url
            assert info["proj-001"]["file_hash"] == "abc123"
            assert info["proj-001"]["entry_point"] == "main.py"

    @pytest.mark.asyncio
    async def test_sync_s3_project_regenerate_url(self, mock_worker, mock_project):
        """测试 S3 项目重新生成预签名 URL"""
        new_presigned_url = "https://s3.example.com/bucket/file.zip?signature=new"

        mock_sync_service = MagicMock()
        mock_sync_service.get_project_transfer_info = AsyncMock(
            return_value={
                "transfer_method": "s3_original",
                "file_path": "projects/proj-001/file.zip",
                "file_hash": "abc123",
                "entry_point": "main.py",
                "presigned_url": None,  # 没有预签名 URL
            }
        )

        with patch(
            "antcode_core.infrastructure.storage.presign.is_s3_storage_enabled",
            return_value=True,
        ), patch(
            "antcode_core.infrastructure.storage.presign.try_generate_download_url",
            new_callable=AsyncMock,
            return_value=new_presigned_url,
        ), patch(
            "antcode_core.domain.models.Project.get_or_none",
            new_callable=AsyncMock,
            return_value=mock_project,
        ), patch(
            "antcode_core.application.services.projects.project_sync_service.project_sync_service",
            mock_sync_service,
        ):
            from antcode_core.application.services.workers.worker_project_sync import (
                WorkerProjectSyncService,
            )

            service = WorkerProjectSyncService()
            results, info = await service.sync_projects_to_worker_with_info(
                mock_worker, ["proj-001"]
            )

            assert "proj-001" in results["synced"]
            assert info["proj-001"]["download_url"] == new_presigned_url

    @pytest.mark.asyncio
    async def test_sync_fails_when_presigned_url_generation_fails(
        self, mock_worker, mock_project
    ):
        """测试预签名 URL 生成失败时同步失败"""
        mock_sync_service = MagicMock()
        mock_sync_service.get_project_transfer_info = AsyncMock(
            return_value={
                "transfer_method": "s3_original",
                "file_path": "projects/proj-001/file.zip",
                "file_hash": "abc123",
                "entry_point": "main.py",
                "presigned_url": None,
            }
        )

        with patch(
            "antcode_core.infrastructure.storage.presign.is_s3_storage_enabled",
            return_value=True,
        ), patch(
            "antcode_core.infrastructure.storage.presign.try_generate_download_url",
            new_callable=AsyncMock,
            return_value=None,  # 生成失败
        ), patch(
            "antcode_core.domain.models.Project.get_or_none",
            new_callable=AsyncMock,
            return_value=mock_project,
        ), patch(
            "antcode_core.application.services.projects.project_sync_service.project_sync_service",
            mock_sync_service,
        ):
            from antcode_core.application.services.workers.worker_project_sync import (
                WorkerProjectSyncService,
            )

            service = WorkerProjectSyncService()
            results, info = await service.sync_projects_to_worker_with_info(
                mock_worker, ["proj-001"]
            )

            assert "proj-001" not in results["synced"]
            assert len(results["failed"]) == 1
            assert "无法生成 S3 预签名 URL" in results["failed"][0]["reason"]


class TestPresignedUrlExpiry:
    """测试预签名 URL 有效期"""

    @pytest.mark.asyncio
    async def test_presigned_url_expires_in_one_hour(self):
        """测试预签名 URL 有效期为 1 小时"""
        # 验证代码中使用的 expires_in=3600
        from antcode_core.application.services.workers.worker_project_sync import (
            WorkerProjectSyncService,
        )
        import inspect

        source = inspect.getsource(WorkerProjectSyncService.sync_projects_to_worker_with_info)
        assert "expires_in=3600" in source, "预签名 URL 有效期应为 3600 秒（1 小时）"

    @pytest.mark.asyncio
    async def test_project_sync_service_uses_one_hour_expiry(self):
        """测试 ProjectSyncService 使用 1 小时有效期"""
        from antcode_core.application.services.projects.project_sync_service import (
            ProjectSyncService,
        )
        import inspect

        source = inspect.getsource(ProjectSyncService)
        # 检查所有 try_generate_download_url 调用都使用 3600
        assert source.count("expires_in=3600") >= 1, "应使用 3600 秒有效期"


class TestTransferMethodHandling:
    """测试不同传输方式的处理"""

    @pytest.fixture
    def mock_worker(self):
        """模拟 Worker 对象"""
        worker = MagicMock()
        worker.id = 1
        worker.public_id = "worker-001"
        worker.name = "Test Worker"
        return worker

    @pytest.fixture
    def mock_project(self):
        """模拟项目对象"""
        project = MagicMock()
        project.id = 1
        project.public_id = "proj-001"
        project.name = "Test Project"
        return project

    @pytest.mark.asyncio
    async def test_s3_original_uses_presigned_url(self, mock_worker, mock_project):
        """测试 s3_original 传输方式使用预签名 URL"""
        presigned_url = "https://s3.example.com/original.zip?sig=xxx"

        mock_sync_service = MagicMock()
        mock_sync_service.get_project_transfer_info = AsyncMock(
            return_value={
                "transfer_method": "s3_original",
                "file_path": "projects/proj-001/original.zip",
                "file_hash": "hash123",
                "entry_point": "main.py",
                "presigned_url": presigned_url,
            }
        )

        with patch(
            "antcode_core.infrastructure.storage.presign.is_s3_storage_enabled",
            return_value=True,
        ), patch(
            "antcode_core.domain.models.Project.get_or_none",
            new_callable=AsyncMock,
            return_value=mock_project,
        ), patch(
            "antcode_core.application.services.projects.project_sync_service.project_sync_service",
            mock_sync_service,
        ):
            from antcode_core.application.services.workers.worker_project_sync import (
                WorkerProjectSyncService,
            )

            service = WorkerProjectSyncService()
            results, info = await service.sync_projects_to_worker_with_info(
                mock_worker, ["proj-001"]
            )

            assert info["proj-001"]["download_url"] == presigned_url

    @pytest.mark.asyncio
    async def test_s3_pack_uses_presigned_url(self, mock_worker, mock_project):
        """测试 s3_pack 传输方式使用预签名 URL"""
        presigned_url = "https://s3.example.com/pack.zip?sig=xxx"

        mock_sync_service = MagicMock()
        mock_sync_service.get_project_transfer_info = AsyncMock(
            return_value={
                "transfer_method": "s3_pack",
                "file_path": "temp/proj-001_pack.zip",
                "file_hash": "hash456",
                "entry_point": "main.py",
                "presigned_url": presigned_url,
            }
        )

        with patch(
            "antcode_core.infrastructure.storage.presign.is_s3_storage_enabled",
            return_value=True,
        ), patch(
            "antcode_core.domain.models.Project.get_or_none",
            new_callable=AsyncMock,
            return_value=mock_project,
        ), patch(
            "antcode_core.application.services.projects.project_sync_service.project_sync_service",
            mock_sync_service,
        ):
            from antcode_core.application.services.workers.worker_project_sync import (
                WorkerProjectSyncService,
            )

            service = WorkerProjectSyncService()
            results, info = await service.sync_projects_to_worker_with_info(
                mock_worker, ["proj-001"]
            )

            assert info["proj-001"]["download_url"] == presigned_url
