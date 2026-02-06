"""
Worker Runtime 单元测试

测试运行时管理功能：
- 运行时规格
- 哈希计算
- 运行时构建
- 运行时管理
"""

import os
import sys
import tempfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from antcode_worker.domain.models import RuntimeHandle
from antcode_worker.runtime.hash import compute_runtime_hash
from antcode_worker.runtime.spec import LockSource, PythonSpec, RuntimeSpec


class TestPythonSpec:
    """Python 规格测试"""

    def test_default_spec(self):
        """测试默认规格"""
        spec = PythonSpec()
        
        assert spec.version is None
        assert spec.path is None

    def test_with_version(self):
        """测试指定版本"""
        spec = PythonSpec(version="3.11")
        
        assert spec.version == "3.11"

    def test_to_dict(self):
        """测试转字典"""
        spec = PythonSpec(version="3.11", path="/usr/bin/python3.11")
        d = spec.to_dict()
        
        assert d["version"] == "3.11"
        assert d["path"] == "/usr/bin/python3.11"

    def test_from_dict(self):
        """测试从字典创建"""
        d = {"version": "3.10", "path": "/usr/bin/python3.10"}
        spec = PythonSpec.from_dict(d)
        
        assert spec.version == "3.10"
        assert spec.path == "/usr/bin/python3.10"


class TestLockSource:
    """锁源测试"""

    def test_requirements_source(self):
        """测试 requirements 源"""
        source = LockSource(
            source_type="requirements",
            requirements=["requests>=2.28", "numpy"],
        )
        
        assert source.source_type == "requirements"
        assert len(source.requirements) == 2

    def test_inline_source(self):
        """测试内联锁文件"""
        lock_content = "# uv.lock content"
        source = LockSource(
            source_type="inline",
            inline_content=lock_content,
        )
        
        assert source.source_type == "inline"
        assert source.inline_content == lock_content

    def test_uri_source(self):
        """测试 URI 锁文件"""
        source = LockSource(
            source_type="uri",
            uri="s3://bucket/project/uv.lock",
        )
        
        assert source.source_type == "uri"
        assert source.uri == "s3://bucket/project/uv.lock"


class TestRuntimeSpec:
    """运行时规格测试"""

    def test_default_spec(self):
        """测试默认规格"""
        spec = RuntimeSpec()
        
        assert spec.python_spec is not None
        assert spec.lock_source is not None

    def test_with_requirements(self):
        """测试带依赖的规格"""
        spec = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource(
                source_type="requirements",
                requirements=["requests", "pandas"],
            ),
        )
        
        assert spec.python_spec.version == "3.11"
        assert len(spec.lock_source.requirements) == 2

    def test_to_dict_from_dict(self):
        """测试序列化和反序列化"""
        spec = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource(
                source_type="requirements",
                requirements=["requests"],
            ),
            constraints=["requests<3.0"],
        )
        
        d = spec.to_dict()
        spec2 = RuntimeSpec.from_dict(d)
        
        assert spec2.python_spec.version == "3.11"
        assert spec2.lock_source.requirements == ["requests"]
        assert spec2.constraints == ["requests<3.0"]


class TestRuntimeHash:
    """运行时哈希测试"""

    def test_same_spec_same_hash(self):
        """测试相同规格产生相同哈希"""
        spec1 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource(
                source_type="requirements",
                requirements=["requests==2.28.0"],
            ),
        )
        
        spec2 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource(
                source_type="requirements",
                requirements=["requests==2.28.0"],
            ),
        )
        
        hash1 = compute_runtime_hash(spec1)
        hash2 = compute_runtime_hash(spec2)
        
        assert hash1 == hash2

    def test_different_spec_different_hash(self):
        """测试不同规格产生不同哈希"""
        spec1 = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource(
                source_type="requirements",
                requirements=["requests==2.28.0"],
            ),
        )
        
        spec2 = RuntimeSpec(
            python_spec=PythonSpec(version="3.10"),
            lock_source=LockSource(
                source_type="requirements",
                requirements=["requests==2.28.0"],
            ),
        )
        
        hash1 = compute_runtime_hash(spec1)
        hash2 = compute_runtime_hash(spec2)
        
        assert hash1 != hash2

    def test_hash_is_deterministic(self):
        """测试哈希是确定性的"""
        spec = RuntimeSpec(
            python_spec=PythonSpec(version="3.11"),
            lock_source=LockSource(
                source_type="requirements",
                requirements=["numpy", "pandas", "requests"],
            ),
        )
        
        hashes = [compute_runtime_hash(spec) for _ in range(10)]
        
        assert len(set(hashes)) == 1


class TestRuntimeHandle:
    """运行时句柄测试"""

    def test_create_handle(self):
        """测试创建句柄"""
        handle = RuntimeHandle(
            path="/path/to/venv",
            runtime_hash="abc123",
            python_executable="/path/to/venv/bin/python",
            python_version="3.11.5",
        )
        
        assert handle.path == "/path/to/venv"
        assert handle.runtime_hash == "abc123"
        assert handle.python_executable == "/path/to/venv/bin/python"
        assert handle.python_version == "3.11.5"

    def test_handle_with_timestamps(self):
        """测试带时间戳的句柄"""
        now = datetime.now()
        handle = RuntimeHandle(
            path="/path/to/venv",
            runtime_hash="abc123",
            python_executable="/path/to/venv/bin/python",
            created_at=now,
            last_used_at=now,
        )
        
        assert handle.created_at == now
        assert handle.last_used_at == now


class TestRuntimeBuilder:
    """运行时构建器测试"""

    @pytest.mark.asyncio
    async def test_builder_exists(self):
        """测试检查运行时是否存在"""
        from antcode_worker.runtime.builder import RuntimeBuilder
        
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = RuntimeBuilder(venvs_dir=tmpdir)
            
            # 不存在的运行时
            assert builder.exists("nonexistent") is False
            
            # 创建一个假的运行时目录
            venv_path = os.path.join(tmpdir, "test-hash")
            os.makedirs(venv_path)
            bin_dir = os.path.join(venv_path, "bin")
            os.makedirs(bin_dir)
            python_path = os.path.join(bin_dir, "python")
            with open(python_path, "w") as f:
                f.write("#!/bin/bash\n")
            
            assert builder.exists("test-hash") is True

    @pytest.mark.asyncio
    async def test_builder_list_runtimes(self):
        """测试列出运行时"""
        from antcode_worker.runtime.builder import RuntimeBuilder
        
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = RuntimeBuilder(venvs_dir=tmpdir)
            
            # 空目录
            runtimes = await builder.list_runtimes()
            assert len(runtimes) == 0


class TestRuntimeManager:
    """运行时管理器测试"""

    @pytest.mark.asyncio
    async def test_manager_start_stop(self):
        """测试管理器启动和停止"""
        from antcode_worker.runtime.manager import RuntimeManager, RuntimeManagerConfig
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = RuntimeManagerConfig(
                venvs_dir=os.path.join(tmpdir, "venvs"),
                locks_dir=os.path.join(tmpdir, "locks"),
                auto_gc=False,
            )
            
            manager = RuntimeManager(config)
            
            await manager.start()
            assert manager._running is True
            
            await manager.stop()
            assert manager._running is False

    @pytest.mark.asyncio
    async def test_manager_exists(self):
        """测试检查运行时是否存在"""
        from antcode_worker.runtime.manager import RuntimeManager, RuntimeManagerConfig
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = RuntimeManagerConfig(
                venvs_dir=os.path.join(tmpdir, "venvs"),
                auto_gc=False,
            )
            
            manager = RuntimeManager(config)
            await manager.start()
            
            try:
                assert manager.exists("nonexistent") is False
            finally:
                await manager.stop()

    @pytest.mark.asyncio
    async def test_manager_get_stats(self):
        """测试获取统计信息"""
        from antcode_worker.runtime.manager import RuntimeManager, RuntimeManagerConfig
        
        with tempfile.TemporaryDirectory() as tmpdir:
            config = RuntimeManagerConfig(
                venvs_dir=os.path.join(tmpdir, "venvs"),
                auto_gc=False,
            )
            
            manager = RuntimeManager(config)
            await manager.start()
            
            try:
                stats = manager.get_stats()
                
                assert "runtime_count" in stats
                assert "total_size_bytes" in stats
                assert "active_count" in stats
                assert "gc" in stats
                assert "locks" in stats
            finally:
                await manager.stop()
