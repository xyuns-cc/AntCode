"""
项目版本管理单元测试

测试：
- DraftManifest 数据结构
- ProjectDraftService 草稿操作
- ProjectVersionService 版本操作
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from antcode_core.application.services.projects.draft_service import (
    DraftManifest,
    ProjectDraftService,
)


class TestDraftManifest:
    """测试 DraftManifest 数据结构"""

    def test_create_empty_manifest(self):
        """测试创建空 manifest"""
        manifest = DraftManifest(project_id=123)
        
        assert manifest.project_id == 123
        assert manifest.kind == "draft"
        assert manifest.files == []
        assert manifest.file_count == 0
        assert manifest.total_size == 0

    def test_set_file(self):
        """测试设置文件"""
        manifest = DraftManifest(project_id=123)
        manifest.set_file("main.py", 1024, "sha256:abc123")
        
        assert manifest.file_count == 1
        assert manifest.total_size == 1024
        
        file_info = manifest.get_file("main.py")
        assert file_info is not None
        assert file_info["size"] == 1024
        assert file_info["hash"] == "sha256:abc123"

    def test_update_existing_file(self):
        """测试更新已存在的文件"""
        manifest = DraftManifest(project_id=123)
        manifest.set_file("main.py", 1024, "sha256:abc123")
        manifest.set_file("main.py", 2048, "sha256:def456")
        
        assert manifest.file_count == 1
        assert manifest.total_size == 2048
        
        file_info = manifest.get_file("main.py")
        assert file_info["size"] == 2048
        assert file_info["hash"] == "sha256:def456"


    def test_remove_file(self):
        """测试删除文件"""
        manifest = DraftManifest(project_id=123)
        manifest.set_file("main.py", 1024, "sha256:abc123")
        manifest.set_file("utils.py", 512, "sha256:def456")
        
        assert manifest.file_count == 2
        
        result = manifest.remove_file("main.py")
        assert result is True
        assert manifest.file_count == 1
        assert manifest.get_file("main.py") is None
        assert manifest.get_file("utils.py") is not None

    def test_remove_nonexistent_file(self):
        """测试删除不存在的文件"""
        manifest = DraftManifest(project_id=123)
        result = manifest.remove_file("nonexistent.py")
        assert result is False

    def test_rename_file(self):
        """测试重命名文件"""
        manifest = DraftManifest(project_id=123)
        manifest.set_file("old_name.py", 1024, "sha256:abc123")
        
        result = manifest.rename_file("old_name.py", "new_name.py")
        assert result is True
        assert manifest.get_file("old_name.py") is None
        assert manifest.get_file("new_name.py") is not None
        assert manifest.get_file("new_name.py")["size"] == 1024

    def test_content_hash(self):
        """测试内容哈希计算"""
        manifest1 = DraftManifest(project_id=123)
        manifest1.set_file("main.py", 1024, "sha256:abc123")
        
        manifest2 = DraftManifest(project_id=123)
        manifest2.set_file("main.py", 1024, "sha256:abc123")
        
        # 相同内容应该有相同的哈希
        assert manifest1.content_hash == manifest2.content_hash

    def test_to_dict(self):
        """测试转换为字典"""
        manifest = DraftManifest(project_id=123)
        manifest.set_file("main.py", 1024, "sha256:abc123")
        
        data = manifest.to_dict()
        
        assert data["schema"] == 1
        assert data["project_id"] == 123
        assert data["kind"] == "draft"
        assert data["file_count"] == 1
        assert data["total_size"] == 1024
        assert len(data["files"]) == 1

    def test_from_dict(self):
        """测试从字典创建"""
        data = {
            "project_id": 123,
            "files": [
                {"path": "main.py", "size": 1024, "hash": "sha256:abc123", "mtime": 12345}
            ],
            "created_at": "2026-01-13T10:00:00",
            "updated_at": "2026-01-13T12:00:00",
        }
        
        manifest = DraftManifest.from_dict(data)
        
        assert manifest.project_id == 123
        assert manifest.file_count == 1
        assert manifest.get_file("main.py") is not None

    def test_to_json(self):
        """测试转换为 JSON"""
        manifest = DraftManifest(project_id=123)
        manifest.set_file("main.py", 1024, "sha256:abc123")
        
        json_str = manifest.to_json()
        data = json.loads(json_str)
        
        assert data["project_id"] == 123
        assert data["file_count"] == 1


class TestProjectDraftService:
    """测试 ProjectDraftService"""

    @pytest.fixture
    def mock_project_file(self):
        """创建模拟的 ProjectFile"""
        pf = MagicMock()
        pf.project_id = 123
        pf.draft_manifest_key = None
        pf.draft_root_prefix = None
        pf.dirty = False
        pf.dirty_files_count = 0
        pf.last_editor_id = None
        pf.last_edit_at = None
        pf.save = AsyncMock()
        return pf

    @pytest.fixture
    def draft_service(self):
        """创建 DraftService 实例"""
        return ProjectDraftService()

    def test_get_draft_prefix(self, draft_service):
        """测试获取草稿前缀"""
        prefix = draft_service._get_draft_prefix(123)
        assert prefix == "projects/123/draft/files/"

    def test_get_draft_manifest_key(self, draft_service):
        """测试获取草稿 manifest 路径"""
        key = draft_service._get_draft_manifest_key(123)
        assert key == "projects/123/draft/manifest.json"

    @pytest.mark.asyncio
    async def test_get_edit_status(self, draft_service, mock_project_file):
        """测试获取编辑状态"""
        mock_project_file.dirty = True
        mock_project_file.dirty_files_count = 3
        mock_project_file.published_version = 2
        
        status = await draft_service.get_edit_status(mock_project_file)
        
        assert status["dirty"] is True
        assert status["dirty_files_count"] == 3
        assert status["published_version"] == 2
