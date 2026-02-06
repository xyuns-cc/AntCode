"""
执行器测试

测试日志接收器和沙箱执行器的功能。
"""

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 添加 worker 源码路径
worker_src = Path(__file__).parent.parent.parent.parent / "services" / "worker" / "src"
if str(worker_src) not in sys.path:
    sys.path.insert(0, str(worker_src))

from antcode_worker.executor.base import (
    BufferedLogSink,
    CallbackLogSink,
    ExecutorConfig,
    ExecutorStats,
    NoOpLogSink,
)
from antcode_worker.executor.sandbox import (
    BasicSandbox,
    NoOpSandbox,
    SandboxConfig,
    create_sandbox,
)


class TestExecutorConfig:
    """执行器配置测试"""

    def test_default_values(self):
        """测试默认值"""
        config = ExecutorConfig()
        assert config.max_concurrent == 5
        assert config.default_timeout == 3600
        assert config.default_grace_period == 10


class TestExecutorStats:
    """执行器统计测试"""

    def test_to_dict(self):
        """测试转换为字典"""
        stats = ExecutorStats(total_executions=10, completed=8, failed=2)
        d = stats.to_dict()

        assert d["total_executions"] == 10
        assert d["completed"] == 8
        assert d["failed"] == 2


class TestNoOpLogSink:
    """空日志接收器测试"""

    @pytest.mark.asyncio
    async def test_write_does_nothing(self):
        """测试写入不做任何事"""
        sink = NoOpLogSink()
        await sink.write(MagicMock())
        await sink.flush()


class TestCallbackLogSink:
    """回调日志接收器测试"""

    @pytest.mark.asyncio
    async def test_sync_callback(self):
        """测试同步回调"""
        entries = []

        def callback(entry):
            entries.append(entry)

        sink = CallbackLogSink(callback)
        entry = MagicMock()
        await sink.write(entry)

        assert len(entries) == 1
        assert entries[0] == entry

    @pytest.mark.asyncio
    async def test_async_callback(self):
        """测试异步回调"""
        entries = []

        async def callback(entry):
            entries.append(entry)

        sink = CallbackLogSink(callback)
        entry = MagicMock()
        await sink.write(entry)

        assert len(entries) == 1


class TestBufferedLogSink:
    """缓冲日志接收器测试"""

    @pytest.mark.asyncio
    async def test_flush_on_buffer_full(self):
        """测试缓冲满时刷新"""
        flushed = []

        def flush_callback(entries):
            flushed.extend(entries)

        sink = BufferedLogSink(flush_callback, max_buffer_size=2)

        await sink.write(MagicMock())
        assert len(flushed) == 0

        await sink.write(MagicMock())
        assert len(flushed) == 2

    @pytest.mark.asyncio
    async def test_manual_flush(self):
        """测试手动刷新"""
        flushed = []

        def flush_callback(entries):
            flushed.extend(entries)

        sink = BufferedLogSink(flush_callback, max_buffer_size=100)

        await sink.write(MagicMock())
        assert len(flushed) == 0

        await sink.flush()
        assert len(flushed) == 1


class TestNoOpSandbox:
    """空操作沙箱测试"""

    @pytest.mark.asyncio
    async def test_prepare(self):
        """测试准备"""
        sandbox = NoOpSandbox()
        exec_plan = MagicMock()
        context = await sandbox.prepare(exec_plan, "/work")

        assert context["work_dir"] == "/work"

    def test_wrap_command(self):
        """测试命令包装"""
        sandbox = NoOpSandbox()
        cmd = ["python", "test.py"]
        wrapped = sandbox.wrap_command(cmd, {})

        assert wrapped == cmd

    def test_filter_env(self):
        """测试环境变量过滤"""
        sandbox = NoOpSandbox()
        env = {"PATH": "/bin", "SECRET": "123"}
        filtered = sandbox.filter_env(env, {})

        assert filtered == env


class TestBasicSandbox:
    """基础沙箱测试"""

    def test_filter_env_allows_safe_vars(self):
        """测试允许安全的环境变量"""
        config = SandboxConfig(allowed_env_vars=["PATH", "HOME"])
        sandbox = BasicSandbox(config)

        env = {"PATH": "/bin", "HOME": "/home/user", "OTHER": "value"}
        filtered = sandbox.filter_env(env, {})

        assert "PATH" in filtered
        assert "HOME" in filtered
        assert "OTHER" not in filtered

    def test_filter_env_blocks_sensitive(self):
        """测试过滤敏感环境变量（修复验证）"""
        config = SandboxConfig(allowed_env_vars=["PATH", "API_KEY", "DATABASE_PASSWORD"])
        sandbox = BasicSandbox(config)

        env = {
            "PATH": "/bin",
            "API_KEY": "secret123",
            "DATABASE_PASSWORD": "pass123",
        }
        filtered = sandbox.filter_env(env, {})

        assert "PATH" in filtered
        assert "API_KEY" not in filtered
        assert "DATABASE_PASSWORD" not in filtered

    def test_wrap_command_with_custom_sandbox(self):
        """测试自定义沙箱命令"""
        config = SandboxConfig(sandbox_command=["firejail", "--quiet"])
        sandbox = BasicSandbox(config)

        cmd = ["python", "test.py"]
        wrapped = sandbox.wrap_command(cmd, {})

        assert wrapped == ["firejail", "--quiet", "python", "test.py"]

    @pytest.mark.asyncio
    async def test_prepare_creates_temp_dir(self):
        """测试准备时创建临时目录"""
        config = SandboxConfig(fs_isolated=True)
        sandbox = BasicSandbox(config)

        exec_plan = MagicMock()
        context = await sandbox.prepare(exec_plan, "/work")

        assert context["temp_work_dir"] is not None
        assert os.path.exists(context["temp_work_dir"])

        await sandbox.cleanup(context)
        assert not os.path.exists(context["temp_work_dir"])


class TestCreateSandbox:
    """沙箱工厂函数测试"""

    def test_create_noop_sandbox(self):
        """测试创建 NoOp 沙箱"""
        sandbox = create_sandbox("noop")
        assert isinstance(sandbox, NoOpSandbox)

    def test_create_basic_sandbox(self):
        """测试创建基础沙箱"""
        sandbox = create_sandbox("basic")
        assert isinstance(sandbox, BasicSandbox)

    def test_create_unknown_raises(self):
        """测试创建未知类型抛出异常"""
        with pytest.raises(ValueError, match="未知的沙箱类型"):
            create_sandbox("unknown")
