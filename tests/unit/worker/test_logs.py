"""
Worker 日志模块单元测试

测试 WAL 和批量发送核心功能
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path

import pytest

from antcode_worker.logs.wal import (
    WALConfig,
    WALEntry,
    WALMetadata,
    WALState,
    WALWriter,
)
from antcode_worker.logs.batch import (
    BackpressureState,
    BatchConfig,
    BatchSender,
)


class TestWALEntry:
    """WAL 条目测试"""

    def test_entry_to_line(self):
        """测试序列化"""
        entry = WALEntry(
            seq=1,
            timestamp=1234567890.0,
            log_type="stdout",
            content="Hello, World!",
            level="INFO",
        )
        
        line = entry.to_line()
        assert line.endswith("\n")
        
        data = json.loads(line.strip())
        assert data["seq"] == 1
        assert data["type"] == "stdout"
        assert data["content"] == "Hello, World!"

    def test_entry_from_line(self):
        """测试反序列化"""
        line = '{"seq": 2, "ts": 1234567890.0, "type": "stderr", "content": "Error!", "level": "ERROR"}'
        
        entry = WALEntry.from_line(line)
        
        assert entry.seq == 2
        assert entry.log_type == "stderr"
        assert entry.content == "Error!"
        assert entry.level == "ERROR"


class TestWALMetadata:
    """WAL 元数据测试"""

    def test_metadata_to_dict(self):
        """测试转字典"""
        meta = WALMetadata(
            run_id="run-001",
            state=WALState.ACTIVE,
            entry_count=100,
            byte_size=1024,
        )
        
        d = meta.to_dict()
        
        assert d["run_id"] == "run-001"
        assert d["state"] == "active"
        assert d["entry_count"] == 100

    def test_metadata_from_dict(self):
        """测试从字典创建"""
        d = {
            "run_id": "run-002",
            "state": "sealed",
            "entry_count": 50,
            "byte_size": 512,
            "checksum": "abc123",
        }
        
        meta = WALMetadata.from_dict(d)
        
        assert meta.run_id == "run-002"
        assert meta.state == WALState.SEALED
        assert meta.entry_count == 50


class TestWALWriter:
    """WAL 写入器测试"""

    @pytest.mark.asyncio
    async def test_writer_start_stop(self):
        """测试启动和停止"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = WALConfig(wal_dir=tmpdir)
            writer = WALWriter("run-001", config)
            
            await writer.start()
            assert writer._running is True
            assert writer.wal_path.parent.exists()
            
            await writer.stop()
            assert writer._running is False

    @pytest.mark.asyncio
    async def test_writer_write(self):
        """测试写入"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = WALConfig(wal_dir=tmpdir, sync_on_write=True)
            writer = WALWriter("run-002", config)
            
            await writer.start()
            
            seq1 = await writer.write("stdout", "Line 1")
            seq2 = await writer.write("stderr", "Error line")
            
            assert seq1 == 1
            assert seq2 == 2
            assert writer.metadata.entry_count == 2
            
            await writer.stop()

    @pytest.mark.asyncio
    async def test_writer_seal(self):
        """测试封存"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = WALConfig(wal_dir=tmpdir, sync_on_write=True)
            writer = WALWriter("run-003", config)
            
            await writer.start()
            await writer.write("stdout", "Test content")
            
            meta = await writer.seal()
            
            assert meta.state == WALState.SEALED
            assert meta.checksum != ""
            assert meta.sealed_at is not None
            
            await writer.stop()


class TestBatchConfig:
    """批量配置测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = BatchConfig()
        
        assert config.batch_size == 100
        assert config.batch_timeout == 1.0
        assert config.max_queue_size == 10000

    def test_custom_config(self):
        """测试自定义配置"""
        config = BatchConfig(
            batch_size=50,
            max_queue_size=5000,
            warning_threshold=0.8,
        )
        
        assert config.batch_size == 50
        assert config.max_queue_size == 5000
        assert config.warning_threshold == 0.8


class TestBackpressureState:
    """Backpressure 状态测试"""

    def test_state_values(self):
        """测试状态值"""
        assert BackpressureState.NORMAL.value == "normal"
        assert BackpressureState.WARNING.value == "warning"
        assert BackpressureState.CRITICAL.value == "critical"
        assert BackpressureState.BLOCKED.value == "blocked"
