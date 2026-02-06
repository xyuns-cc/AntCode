"""S3 预签名 URL 单元测试

测试预签名 URL 生成和项目同步逻辑。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestPresignModule:
    """测试 presign 模块"""

    @pytest.fixture
    def mock_local_backend(self):
        """模拟本地存储后端"""
        backend = MagicMock()
        backend.is_s3_backend = MagicMock(return_value=False)
        return backend

    @pytest.fixture
    def mock_s3_backend(self):
        """模拟 S3 存储后端"""
        backend = MagicMock()
        backend.is_s3_backend = MagicMock(return_value=True)
        backend.generate_presigned_url = AsyncMock(
            return_value="https://s3.example.com/bucket/file.zip?signature=xxx"
        )
        backend.build_path = MagicMock(return_value="files/2025/01/13/test.zip")
        return backend

    @pytest.mark.asyncio
    async def test_is_s3_storage_enabled_with_local(self, mock_local_backend):
        """测试本地存储时 is_s3_storage_enabled 返回 False"""
        with patch(
            "antcode_core.infrastructure.storage.presign.get_file_storage_backend",
            return_value=mock_local_backend,
        ):
            from antcode_core.infrastructure.storage.presign import is_s3_storage_enabled

            assert is_s3_storage_enabled() is False

    @pytest.mark.asyncio
    async def test_is_s3_storage_enabled_with_s3(self, mock_s3_backend):
        """测试 S3 存储时 is_s3_storage_enabled 返回 True"""
        with patch(
            "antcode_core.infrastructure.storage.presign.get_file_storage_backend",
            return_value=mock_s3_backend,
        ):
            from antcode_core.infrastructure.storage.presign import is_s3_storage_enabled

            assert is_s3_storage_enabled() is True

    @pytest.mark.asyncio
    async def test_generate_download_url_success(self, mock_s3_backend):
        """测试成功生成下载预签名 URL"""
        with patch(
            "antcode_core.infrastructure.storage.presign.get_file_storage_backend",
            return_value=mock_s3_backend,
        ):
            from antcode_core.infrastructure.storage.presign import generate_download_url

            url = await generate_download_url("files/test.zip", expires_in=3600)

            assert url == "https://s3.example.com/bucket/file.zip?signature=xxx"
            mock_s3_backend.generate_presigned_url.assert_called_once_with(
                "files/test.zip",
                expires_in=3600,
                method="get_object",
            )

    @pytest.mark.asyncio
    async def test_generate_download_url_not_supported(self):
        """测试本地存储不支持预签名 URL"""
        # 创建一个没有 generate_presigned_url 方法的后端
        backend = MagicMock(spec=[])  # 空 spec，没有任何方法
        
        with patch(
            "antcode_core.infrastructure.storage.presign.get_file_storage_backend",
            return_value=backend,
        ):
            from antcode_core.infrastructure.storage.presign import generate_download_url

            with pytest.raises(NotImplementedError):
                await generate_download_url("files/test.zip")

    @pytest.mark.asyncio
    async def test_try_generate_download_url_success(self, mock_s3_backend):
        """测试 try_generate_download_url 成功时返回 URL"""
        with patch(
            "antcode_core.infrastructure.storage.presign.get_file_storage_backend",
            return_value=mock_s3_backend,
        ):
            from antcode_core.infrastructure.storage.presign import try_generate_download_url

            url = await try_generate_download_url("files/test.zip", expires_in=3600)

            assert url == "https://s3.example.com/bucket/file.zip?signature=xxx"

    @pytest.mark.asyncio
    async def test_try_generate_download_url_fallback(self, mock_local_backend):
        """测试 try_generate_download_url 失败时返回回退 URL"""
        with patch(
            "antcode_core.infrastructure.storage.presign.get_file_storage_backend",
            return_value=mock_local_backend,
        ):
            from antcode_core.infrastructure.storage.presign import try_generate_download_url

            fallback = "http://api.example.com/download/test.zip"
            url = await try_generate_download_url(
                "files/test.zip", fallback_url=fallback
            )

            assert url == fallback

    @pytest.mark.asyncio
    async def test_try_generate_download_url_no_fallback(self, mock_local_backend):
        """测试 try_generate_download_url 失败且无回退时返回 None"""
        with patch(
            "antcode_core.infrastructure.storage.presign.get_file_storage_backend",
            return_value=mock_local_backend,
        ):
            from antcode_core.infrastructure.storage.presign import try_generate_download_url

            url = await try_generate_download_url("files/test.zip")

            assert url is None

    @pytest.mark.asyncio
    async def test_generate_upload_url_success(self, mock_s3_backend):
        """测试成功生成上传预签名 URL"""
        mock_s3_backend.generate_presigned_url = AsyncMock(
            return_value="https://s3.example.com/bucket/upload?signature=yyy"
        )

        with patch(
            "antcode_core.infrastructure.storage.presign.get_file_storage_backend",
            return_value=mock_s3_backend,
        ):
            from antcode_core.infrastructure.storage.presign import generate_upload_url

            result = await generate_upload_url("test.zip", expires_in=3600)

            assert result["url"] == "https://s3.example.com/bucket/upload?signature=yyy"
            assert result["path"] == "files/2025/01/13/test.zip"
            mock_s3_backend.generate_presigned_url.assert_called_once()


class TestS3FileStorageBackend:
    """测试 S3 文件存储后端"""

    @pytest.fixture
    def mock_s3_client(self):
        """模拟 S3 客户端"""
        client = AsyncMock()
        client.generate_presigned_url = AsyncMock(
            return_value="https://s3.example.com/presigned"
        )
        client.head_object = AsyncMock(return_value={"ContentLength": 1024})
        client.get_object = AsyncMock()
        client.put_object = AsyncMock()
        client.delete_object = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_generate_presigned_url(self, mock_s3_client):
        """测试生成预签名 URL"""
        with patch(
            "antcode_core.infrastructure.storage.s3.get_s3_client_manager"
        ) as mock_manager:
            mock_manager.return_value.get_client = AsyncMock(return_value=mock_s3_client)

            from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend

            backend = S3FileStorageBackend(bucket="test-bucket")
            backend._client_manager = mock_manager.return_value

            url = await backend.generate_presigned_url(
                "files/test.zip", expires_in=3600, method="get_object"
            )

            assert url == "https://s3.example.com/presigned"
            mock_s3_client.generate_presigned_url.assert_called_once_with(
                "get_object",
                Params={"Bucket": "test-bucket", "Key": "files/test.zip"},
                ExpiresIn=3600,
            )

    @pytest.mark.asyncio
    async def test_exists_true(self, mock_s3_client):
        """测试文件存在检查 - 存在"""
        with patch(
            "antcode_core.infrastructure.storage.s3.get_s3_client_manager"
        ) as mock_manager:
            mock_manager.return_value.get_client = AsyncMock(return_value=mock_s3_client)

            from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend

            backend = S3FileStorageBackend(bucket="test-bucket")
            backend._client_manager = mock_manager.return_value

            exists = await backend.exists("files/test.zip")

            assert exists is True

    @pytest.mark.asyncio
    async def test_exists_false(self, mock_s3_client):
        """测试文件存在检查 - 不存在"""
        mock_s3_client.head_object = AsyncMock(side_effect=Exception("NoSuchKey"))

        with patch(
            "antcode_core.infrastructure.storage.s3.get_s3_client_manager"
        ) as mock_manager:
            mock_manager.return_value.get_client = AsyncMock(return_value=mock_s3_client)

            from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend

            backend = S3FileStorageBackend(bucket="test-bucket")
            backend._client_manager = mock_manager.return_value

            exists = await backend.exists("files/nonexistent.zip")

            assert exists is False

    def test_is_s3_backend(self):
        """测试 is_s3_backend 返回 True"""
        with patch("antcode_core.infrastructure.storage.s3.get_s3_client_manager"):
            from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend

            backend = S3FileStorageBackend(bucket="test-bucket")
            assert backend.is_s3_backend() is True

    def test_build_path(self):
        """测试构建存储路径"""
        with patch("antcode_core.infrastructure.storage.s3.get_s3_client_manager"):
            from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend

            backend = S3FileStorageBackend(bucket="test-bucket")
            path = backend.build_path("test.zip")

            assert path.startswith("files/")
            assert path.endswith(".zip")

    def test_validate_file_type(self):
        """测试文件类型验证"""
        with patch("antcode_core.infrastructure.storage.s3.get_s3_client_manager"):
            from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend

            backend = S3FileStorageBackend(
                bucket="test-bucket",
                allowed_extensions=[".zip", ".tar.gz", ".py"],
            )

            assert backend.validate_file_type("test.zip") is True
            assert backend.validate_file_type("test.tar.gz") is True
            assert backend.validate_file_type("test.py") is True
            assert backend.validate_file_type("test.exe") is False
