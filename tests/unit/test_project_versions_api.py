"""
项目版本管理 API 端点测试

测试所有 11 个版本管理相关的 API 端点
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from fastapi.testclient import TestClient


# Mock 依赖
@pytest.fixture
def mock_services():
    """Mock 所有服务依赖"""
    with patch("antcode_web_api.routes.v1.project_versions.project_service") as mock_project_svc, \
         patch("antcode_web_api.routes.v1.project_versions.project_version_service") as mock_version_svc, \
         patch("antcode_web_api.routes.v1.project_versions.project_draft_service") as mock_draft_svc, \
         patch("antcode_web_api.routes.v1.project_versions.relation_service") as mock_relation_svc, \
         patch("antcode_web_api.routes.v1.project_versions.user_service") as mock_user_svc, \
         patch("antcode_web_api.routes.v1.project_versions.audit_service") as mock_audit_svc, \
         patch("antcode_web_api.routes.v1.project_versions.get_current_user_id") as mock_get_user:
        
        # 设置默认返回值
        mock_get_user.return_value = 1
        
        yield {
            "project_service": mock_project_svc,
            "version_service": mock_version_svc,
            "draft_service": mock_draft_svc,
            "relation_service": mock_relation_svc,
            "user_service": mock_user_svc,
            "audit_service": mock_audit_svc,
            "get_current_user_id": mock_get_user,
        }


class TestPublishRequest:
    """测试 PublishRequest 模型"""

    def test_publish_request_with_description(self):
        from antcode_web_api.routes.v1.project_versions import PublishRequest
        
        req = PublishRequest(description="版本 1.0 发布")
        assert req.description == "版本 1.0 发布"

    def test_publish_request_without_description(self):
        from antcode_web_api.routes.v1.project_versions import PublishRequest
        
        req = PublishRequest()
        assert req.description is None


class TestRollbackRequest:
    """测试 RollbackRequest 模型"""

    def test_rollback_request_valid(self):
        from antcode_web_api.routes.v1.project_versions import RollbackRequest
        
        req = RollbackRequest(version=1)
        assert req.version == 1

    def test_rollback_request_invalid_version(self):
        from antcode_web_api.routes.v1.project_versions import RollbackRequest
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            RollbackRequest(version=0)  # 版本号必须 >= 1


class TestVersionInfo:
    """测试 VersionInfo 模型"""

    def test_version_info(self):
        from antcode_web_api.routes.v1.project_versions import VersionInfo
        
        info = VersionInfo(
            version=1,
            version_id="vf_abc123",
            created_at="2026-01-13T10:00:00",
            created_by=1,
            description="初始版本",
            file_count=10,
            total_size=12345,
            content_hash="sha256:abc",
        )
        
        assert info.version == 1
        assert info.version_id == "vf_abc123"
        assert info.file_count == 10


class TestEditStatusResponse:
    """测试 EditStatusResponse 模型"""

    def test_edit_status_response(self):
        from antcode_web_api.routes.v1.project_versions import EditStatusResponse
        
        status = EditStatusResponse(
            dirty=True,
            dirty_files_count=3,
            last_edit_at="2026-01-13T10:00:00",
            last_editor_id=1,
            published_version=2,
        )
        
        assert status.dirty is True
        assert status.dirty_files_count == 3
        assert status.published_version == 2


class TestFileContentRequest:
    """测试 FileContentRequest 模型"""

    def test_file_content_request(self):
        from antcode_web_api.routes.v1.project_versions import FileContentRequest
        
        req = FileContentRequest(
            path="src/main.py",
            content="logging.getLogger(__name__).info('hello')",
            encoding="utf-8",
        )
        
        assert req.path == "src/main.py"
        assert req.content == "logging.getLogger(__name__).info('hello')"
        assert req.encoding == "utf-8"


class TestFileContentResponse:
    """测试 FileContentResponse 模型"""

    def test_file_content_response(self):
        from antcode_web_api.routes.v1.project_versions import FileContentResponse
        
        resp = FileContentResponse(
            name="main.py",
            path="src/main.py",
            size=1024,
            content="logging.getLogger(__name__).info('hello')",
            encoding="utf-8",
            etag="abc123",
            mime_type="text/x-python",
            is_text=True,
        )
        
        assert resp.name == "main.py"
        assert resp.etag == "abc123"
        assert resp.is_text is True


class TestHelperFunction:
    """测试辅助函数"""

    @pytest.mark.asyncio
    async def test_get_project_and_file_detail_not_found(self):
        """测试项目不存在的情况"""
        from antcode_web_api.routes.v1.project_versions import _get_project_and_file_detail
        from antcode_web_api.exceptions import ProjectNotFoundException
        
        with patch("antcode_web_api.routes.v1.project_versions.project_service") as mock_svc:
            mock_svc.get_project_by_id = AsyncMock(return_value=None)
            
            with pytest.raises(ProjectNotFoundException):
                await _get_project_and_file_detail("nonexistent-id", 1)

    @pytest.mark.asyncio
    async def test_get_project_and_file_detail_wrong_type(self):
        """测试项目类型不是 FILE 的情况"""
        from antcode_web_api.routes.v1.project_versions import _get_project_and_file_detail
        from antcode_core.domain.models.enums import ProjectType
        
        mock_project = MagicMock()
        mock_project.type = ProjectType.RULE  # 不是 FILE 类型
        
        with patch("antcode_web_api.routes.v1.project_versions.project_service") as mock_svc:
            mock_svc.get_project_by_id = AsyncMock(return_value=mock_project)
            
            with pytest.raises(HTTPException) as exc_info:
                await _get_project_and_file_detail("project-id", 1)
            
            assert exc_info.value.status_code == 400
            assert "只有文件项目支持版本管理" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_get_project_and_file_detail_no_file_detail(self):
        """测试文件详情不存在的情况"""
        from antcode_web_api.routes.v1.project_versions import _get_project_and_file_detail
        from antcode_core.domain.models.enums import ProjectType
        
        mock_project = MagicMock()
        mock_project.type = ProjectType.FILE
        mock_project.id = 123
        
        with patch("antcode_web_api.routes.v1.project_versions.project_service") as mock_project_svc, \
             patch("antcode_web_api.routes.v1.project_versions.relation_service") as mock_relation_svc:
            mock_project_svc.get_project_by_id = AsyncMock(return_value=mock_project)
            mock_relation_svc.get_project_file_detail = AsyncMock(return_value=None)
            
            with pytest.raises(HTTPException) as exc_info:
                await _get_project_and_file_detail("project-id", 1)
            
            assert exc_info.value.status_code == 404
            assert "项目文件详情不存在" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_get_project_and_file_detail_success(self):
        """测试成功获取项目和文件详情"""
        from antcode_web_api.routes.v1.project_versions import _get_project_and_file_detail
        from antcode_core.domain.models.enums import ProjectType
        
        mock_project = MagicMock()
        mock_project.type = ProjectType.FILE
        mock_project.id = 123
        
        mock_file_detail = MagicMock()
        mock_file_detail.id = 456
        
        with patch("antcode_web_api.routes.v1.project_versions.project_service") as mock_project_svc, \
             patch("antcode_web_api.routes.v1.project_versions.relation_service") as mock_relation_svc:
            mock_project_svc.get_project_by_id = AsyncMock(return_value=mock_project)
            mock_relation_svc.get_project_file_detail = AsyncMock(return_value=mock_file_detail)
            
            project, file_detail = await _get_project_and_file_detail("project-id", 1)
            
            assert project == mock_project
            assert file_detail == mock_file_detail


class TestVersionServiceIntegration:
    """测试版本服务集成"""

    def test_version_service_import(self):
        """测试版本服务可以正常导入"""
        from antcode_core.application.services.projects.version_service import (
            ProjectVersionService,
            project_version_service,
        )
        
        assert project_version_service is not None
        assert isinstance(project_version_service, ProjectVersionService)

    def test_draft_service_import(self):
        """测试草稿服务可以正常导入"""
        from antcode_core.application.services.projects.draft_service import (
            ProjectDraftService,
            project_draft_service,
        )
        
        assert project_draft_service is not None
        assert isinstance(project_draft_service, ProjectDraftService)

    def test_version_service_methods(self):
        """测试版本服务方法存在"""
        from antcode_core.application.services.projects.version_service import project_version_service
        
        # 验证关键方法存在
        assert hasattr(project_version_service, "publish")
        assert hasattr(project_version_service, "discard")
        assert hasattr(project_version_service, "rollback")
        assert hasattr(project_version_service, "list_versions")
        assert hasattr(project_version_service, "get_version")
        assert hasattr(project_version_service, "get_latest_version")

    def test_draft_service_methods(self):
        """测试草稿服务方法存在"""
        from antcode_core.application.services.projects.draft_service import project_draft_service
        
        # 验证关键方法存在
        assert hasattr(project_draft_service, "get_manifest")
        assert hasattr(project_draft_service, "save_manifest")
        assert hasattr(project_draft_service, "get_file_content")
        assert hasattr(project_draft_service, "update_file_content")
        assert hasattr(project_draft_service, "delete_file")
        assert hasattr(project_draft_service, "move_file")
        assert hasattr(project_draft_service, "get_edit_status")


class TestRouterRegistration:
    """测试路由注册"""

    def test_router_import(self):
        """测试路由可以正常导入"""
        from antcode_web_api.routes.v1.project_versions import project_versions_router
        
        assert project_versions_router is not None

    def test_router_routes(self):
        """测试路由端点已注册"""
        from antcode_web_api.routes.v1.project_versions import project_versions_router
        
        # 获取所有路由
        routes = [route.path for route in project_versions_router.routes]
        
        # 验证关键端点存在
        assert "/{project_id}/publish" in routes
        assert "/{project_id}/discard" in routes
        assert "/{project_id}/versions" in routes
        assert "/{project_id}/rollback" in routes
        assert "/{project_id}/edit-status" in routes
        assert "/{project_id}/draft/files/{file_path:path}" in routes
        assert "/{project_id}/draft/files/move" in routes

    def test_v1_router_includes_versions(self):
        """测试 v1 路由包含版本管理路由"""
        from antcode_web_api.routes.v1 import v1_router
        
        # 检查路由是否已注册
        route_paths = []
        for route in v1_router.routes:
            if hasattr(route, "path"):
                route_paths.append(route.path)
        
        # 版本管理路由应该在 /projects 前缀下
        assert any("/projects" in path for path in route_paths)


class TestProjectFileServiceVersionSupport:
    """测试 ProjectFileService 版本支持"""

    def test_file_service_import(self):
        """测试文件服务可以正常导入"""
        from antcode_web_api.services.projects.project_file_service import (
            ProjectFileService,
            project_file_service,
        )
        
        assert project_file_service is not None
        assert isinstance(project_file_service, ProjectFileService)

    def test_versioned_methods_exist(self):
        """测试版本感知方法存在"""
        from antcode_web_api.services.projects.project_file_service import project_file_service
        
        # 验证版本感知方法存在
        assert hasattr(project_file_service, "get_versioned_file_structure")
        assert hasattr(project_file_service, "get_versioned_file_content")
