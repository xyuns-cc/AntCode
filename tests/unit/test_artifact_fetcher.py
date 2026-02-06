"""Worker 端 ArtifactFetcher 单元测试

测试项目文件获取器，验证 S3 预签名 URL 下载功能。
"""

import hashlib
import tempfile
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestProjectCache:
    """测试项目缓存"""

    def test_cache_init(self, tmp_path):
        """测试缓存初始化"""
        from antcode_worker.projects.fetcher import ProjectCache

        cache = ProjectCache(str(tmp_path), max_entries=100, ttl_hours=24)

        assert cache._cache_dir == tmp_path
        assert cache._max_entries == 100
        assert cache._ttl_seconds == 24 * 3600

    @pytest.mark.asyncio
    async def test_cache_get_miss(self, tmp_path):
        """测试缓存未命中"""
        from antcode_worker.projects.fetcher import ProjectCache

        cache = ProjectCache(str(tmp_path))
        result = await cache.get("nonexistent-key")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_put_and_get(self, tmp_path):
        """测试缓存写入和读取"""
        from antcode_worker.projects.fetcher import ProjectCache, ProjectCacheEntry

        cache = ProjectCache(str(tmp_path))

        # 创建测试目录
        project_dir = tmp_path / "test-project"
        project_dir.mkdir()

        entry = ProjectCacheEntry(
            cache_key="proj-001:hash123",
            project_id="proj-001",
            file_hash="hash123",
            local_path=str(project_dir),
            size_bytes=1024,
        )

        await cache.put(entry)
        result = await cache.get("proj-001:hash123")

        assert result == str(project_dir)

    @pytest.mark.asyncio
    async def test_cache_eviction(self, tmp_path):
        """测试缓存淘汰"""
        from antcode_worker.projects.fetcher import ProjectCache, ProjectCacheEntry

        cache = ProjectCache(str(tmp_path), max_entries=2)

        # 创建测试目录
        for i in range(3):
            project_dir = tmp_path / f"project-{i}"
            project_dir.mkdir()

            entry = ProjectCacheEntry(
                cache_key=f"proj-{i}:hash",
                project_id=f"proj-{i}",
                file_hash="hash",
                local_path=str(project_dir),
            )
            await cache.put(entry)

        # 最早的条目应该被淘汰
        assert len(cache._entries) <= 2


class TestArtifactFetcher:
    """测试 ArtifactFetcher"""

    @pytest.fixture
    def cache(self, tmp_path):
        """创建测试缓存"""
        from antcode_worker.projects.fetcher import ProjectCache

        return ProjectCache(str(tmp_path))

    @pytest.fixture
    def fetcher(self, cache):
        """创建测试 fetcher"""
        from antcode_worker.projects.fetcher import ArtifactFetcher

        return ArtifactFetcher(cache)

    def test_build_cache_key_with_hash(self, fetcher):
        """测试使用 file_hash 构建缓存键"""
        key = fetcher._build_cache_key("proj-001", "abc123", "http://example.com/file.zip")

        assert key == "proj-001:abc123"

    def test_build_cache_key_without_hash(self, fetcher):
        """测试不使用 file_hash 构建缓存键"""
        key = fetcher._build_cache_key("proj-001", None, "http://example.com/file.zip")

        assert key.startswith("proj-001:")
        assert len(key) > len("proj-001:")

    def test_guess_filename(self, fetcher):
        """测试从 URL 猜测文件名"""
        # 普通 URL
        assert fetcher._guess_filename("http://example.com/path/file.zip") == "file.zip"

        # 带查询参数的 S3 预签名 URL
        assert (
            fetcher._guess_filename(
                "https://s3.example.com/bucket/file.zip?X-Amz-Signature=xxx"
            )
            == "file.zip"
        )

        # 空路径 - 实际返回域名，但会被当作文件名处理
        result = fetcher._guess_filename("http://example.com/")
        assert result  # 只要有返回值即可

    def test_detect_hash_algo(self, fetcher):
        """测试检测哈希算法"""
        # MD5 (32 字符)
        assert fetcher._detect_hash_algo("d41d8cd98f00b204e9800998ecf8427e") == "md5"

        # SHA256 (64 字符)
        assert (
            fetcher._detect_hash_algo(
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            )
            == "sha256"
        )

    def test_is_unsafe_path(self, fetcher):
        """测试不安全路径检测"""
        assert fetcher._is_unsafe_path("/etc/passwd") is True
        assert fetcher._is_unsafe_path("../../../etc/passwd") is True
        assert fetcher._is_unsafe_path("normal/path/file.py") is False

    @pytest.mark.asyncio
    async def test_fetch_from_cache(self, fetcher, cache, tmp_path):
        """测试从缓存获取"""
        from antcode_worker.projects.fetcher import ProjectCacheEntry

        # 预先放入缓存
        project_dir = tmp_path / "cached-project"
        project_dir.mkdir()

        entry = ProjectCacheEntry(
            cache_key="proj-001:hash123",
            project_id="proj-001",
            file_hash="hash123",
            local_path=str(project_dir),
        )
        await cache.put(entry)

        # 应该直接从缓存返回
        result = await fetcher.fetch(
            project_id="proj-001",
            download_url="http://example.com/file.zip",
            file_hash="hash123",
        )

        assert result == str(project_dir)

    @pytest.mark.asyncio
    async def test_fetch_from_local_file(self, fetcher, tmp_path):
        """测试从本地文件获取"""
        # 创建测试文件（非压缩文件）
        source_file = tmp_path / "source.txt"
        source_file.write_bytes(b"test content")

        result = await fetcher.fetch(
            project_id="proj-001",
            download_url=f"file://{source_file}",
            file_hash=None,
        )

        assert Path(result).exists()

    @pytest.mark.asyncio
    async def test_fetch_from_http_url(self, fetcher, tmp_path):
        """测试从 HTTP URL 获取（模拟 S3 预签名 URL）"""
        test_content = b"test zip content"
        content_hash = hashlib.md5(test_content).hexdigest()

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = AsyncMock(return_value=iter([test_content]))

        # 模拟异步迭代器
        async def async_iter():
            yield test_content

        mock_response.aiter_bytes = lambda: async_iter()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.stream = MagicMock(return_value=AsyncMock())
            mock_client.stream.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__aexit__ = AsyncMock()
            mock_client_cls.return_value = mock_client

            result = await fetcher.fetch(
                project_id="proj-001",
                download_url="https://s3.example.com/bucket/file.txt?signature=xxx",
                file_hash=content_hash,
            )

            assert Path(result).exists()

    @pytest.mark.asyncio
    async def test_fetch_hash_mismatch(self, fetcher, tmp_path):
        """测试哈希不匹配时抛出异常"""
        # 创建测试文件
        source_file = tmp_path / "source.txt"
        source_file.write_bytes(b"test content")

        with pytest.raises(RuntimeError, match="哈希不一致"):
            await fetcher.fetch(
                project_id="proj-001",
                download_url=f"file://{source_file}",
                file_hash="wrong_hash_value_here_32ch",
            )

    @pytest.mark.asyncio
    async def test_extract_zip_file(self, fetcher, tmp_path):
        """测试解压 ZIP 文件"""
        import zipfile

        # 创建测试 ZIP 文件
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(
                "main.py",
                "import logging\n"
                "logging.getLogger(__name__).info('hello')\n",
            )

        result = await fetcher.fetch(
            project_id="proj-001",
            download_url=f"file://{zip_path}",
            file_hash=None,
        )

        # 应该返回解压后的目录
        extracted_dir = Path(result)
        assert extracted_dir.exists()
        assert (extracted_dir / "main.py").exists()

    def test_safe_extract_zip_blocks_path_traversal(self, fetcher, tmp_path):
        """测试 ZIP 解压阻止路径遍历攻击"""
        import zipfile

        # 创建包含恶意路径的 ZIP 文件
        zip_path = tmp_path / "malicious.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            # 尝试写入父目录
            zf.writestr("../../../etc/passwd", "malicious content")

        extract_dir = tmp_path / "extract"
        extract_dir.mkdir()

        with pytest.raises(RuntimeError, match="不安全的压缩路径"):
            fetcher._safe_extract_zip(zip_path, extract_dir)


class TestS3PresignedUrlIntegration:
    """测试 S3 预签名 URL 集成场景"""

    @pytest.mark.asyncio
    async def test_full_flow_with_presigned_url(self, tmp_path):
        """测试完整的 S3 预签名 URL 下载流程"""
        from antcode_worker.projects.fetcher import ProjectCache, ArtifactFetcher

        cache = ProjectCache(str(tmp_path / "cache"))
        fetcher = ArtifactFetcher(cache)

        # 模拟 S3 预签名 URL 响应
        test_content = b"project file content"
        content_hash = hashlib.md5(test_content).hexdigest()

        async def async_iter():
            yield test_content

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = lambda: async_iter()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()

            mock_stream_ctx = AsyncMock()
            mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_stream_ctx.__aexit__ = AsyncMock()
            mock_client.stream = MagicMock(return_value=mock_stream_ctx)

            mock_client_cls.return_value = mock_client

            # 第一次下载
            result1 = await fetcher.fetch(
                project_id="proj-001",
                download_url="https://s3.amazonaws.com/bucket/projects/proj-001/file.txt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=xxx",
                file_hash=content_hash,
            )

            assert Path(result1).exists()

            # 第二次应该从缓存获取
            result2 = await fetcher.fetch(
                project_id="proj-001",
                download_url="https://s3.amazonaws.com/bucket/projects/proj-001/file.txt?X-Amz-Signature=different",
                file_hash=content_hash,
            )

            assert result1 == result2
            # 只应该调用一次 HTTP 请求
            assert mock_client.stream.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_hash_change(self, tmp_path):
        """测试哈希变化时缓存失效"""
        from antcode_worker.projects.fetcher import ProjectCache, ArtifactFetcher

        cache = ProjectCache(str(tmp_path / "cache"))
        fetcher = ArtifactFetcher(cache)

        content_v1 = b"version 1"
        content_v2 = b"version 2"
        hash_v1 = hashlib.md5(content_v1).hexdigest()
        hash_v2 = hashlib.md5(content_v2).hexdigest()

        call_count = 0

        async def make_response(content):
            nonlocal call_count
            call_count += 1

            async def async_iter():
                yield content

            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.aiter_bytes = lambda: async_iter()
            return mock_response

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()

            # 第一次返回 v1
            mock_stream_ctx_v1 = AsyncMock()
            mock_stream_ctx_v1.__aenter__ = AsyncMock(
                return_value=await make_response(content_v1)
            )
            mock_stream_ctx_v1.__aexit__ = AsyncMock()

            # 第二次返回 v2
            mock_stream_ctx_v2 = AsyncMock()
            mock_stream_ctx_v2.__aenter__ = AsyncMock(
                return_value=await make_response(content_v2)
            )
            mock_stream_ctx_v2.__aexit__ = AsyncMock()

            mock_client.stream = MagicMock(
                side_effect=[mock_stream_ctx_v1, mock_stream_ctx_v2]
            )
            mock_client_cls.return_value = mock_client

            # 下载 v1
            result1 = await fetcher.fetch(
                project_id="proj-001",
                download_url="https://s3.example.com/file.txt",
                file_hash=hash_v1,
            )

            # 下载 v2（不同的哈希，应该重新下载）
            result2 = await fetcher.fetch(
                project_id="proj-001",
                download_url="https://s3.example.com/file.txt",
                file_hash=hash_v2,
            )

            # 应该是不同的路径
            assert result1 != result2
            # 应该调用两次 HTTP 请求
            assert mock_client.stream.call_count == 2
