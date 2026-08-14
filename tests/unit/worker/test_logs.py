"""Worker 日志模块单元测试。"""

from unittest.mock import AsyncMock

import pytest
from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import LogEntry
from antcode_worker.logs.batch import (
    BackpressureState,
    BatchConfig,
    BatchSender,
)
from antcode_worker.logs.manager import MAX_DISPATCH_QUEUE_SIZE, LogManager, LogManagerConfig
from antcode_worker.logs.realtime import RealtimeConfig
from antcode_worker.transport.base import GenerationLostError


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


class ConnectedTransport:
    is_connected = True

    def __init__(self, *, send_log: bool = True, send_batch: bool = True):
        self.send_log = AsyncMock(return_value=send_log)
        self.send_log_batch = AsyncMock(return_value=send_batch)


@pytest.mark.asyncio
async def test_log_manager_exposes_realtime_send_failure():
    transport = ConnectedTransport(send_log=False)
    manager = LogManager(
        run_id="run-1",
        transport=transport,
        config=LogManagerConfig(enable_batch=False),
    )
    await manager.start()

    with pytest.raises(RuntimeError, match="实时日志上报失败"):
        await manager.write(LogEntry(run_id="run-1", stream=LogStream.STDOUT, content="line", seq=1))


@pytest.mark.asyncio
async def test_batch_sender_keeps_failed_batch_queued():
    transport = ConnectedTransport(send_batch=False)
    sender = BatchSender(
        run_id="run-1",
        transport=transport,
        config=BatchConfig(batch_size=1, max_retries=1),
    )
    sender._running = True
    await sender.write(LogEntry(run_id="run-1", stream=LogStream.STDOUT, content="line", seq=1))

    with pytest.raises(RuntimeError, match="批量日志发送失败"):
        await sender.flush()

    assert sender.queue_size == 1


@pytest.mark.asyncio
async def test_batch_sender_does_not_retry_permanent_log_error():
    transport = ConnectedTransport()
    transport.send_log_batch.side_effect = ValueError("LogEntry content bytes 超限")
    sender = BatchSender(
        run_id="run-1",
        transport=transport,
        config=BatchConfig(batch_size=1, max_retries=3),
    )
    sender._running = True
    await sender.write(LogEntry(run_id="run-1", stream=LogStream.STDOUT, content="你", seq=1))

    with pytest.raises(ValueError, match="LogEntry content bytes 超限"):
        await sender.flush()
    with pytest.raises(ValueError, match="LogEntry content bytes 超限"):
        await sender.stop()

    assert transport.send_log_batch.await_count == 1
    assert sender.queue_size == 1


@pytest.mark.asyncio
async def test_batch_sender_does_not_retry_or_wrap_generation_loss():
    transport = ConnectedTransport()
    transport.send_log_batch.side_effect = GenerationLostError("superseded")
    sender = BatchSender(
        run_id="run-1",
        transport=transport,
        config=BatchConfig(batch_size=1, max_retries=3),
    )
    sender._running = True
    await sender.write(LogEntry(run_id="run-1", stream=LogStream.STDOUT, content="line", seq=1))

    with pytest.raises(GenerationLostError, match="superseded"):
        await sender.flush()

    transport.send_log_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_log_manager_stop_exposes_background_dispatch_failure():
    transport = ConnectedTransport(send_log=False)
    manager = LogManager(
        run_id="run-1",
        transport=transport,
        config=LogManagerConfig(
            enable_batch=False,
            realtime_config=RealtimeConfig(max_retries=1, retry_delay=0),
        ),
    )
    await manager.start()

    manager._on_log_entry(LogEntry(run_id="run-1", stream=LogStream.STDOUT, content="line", seq=1))

    with pytest.raises(RuntimeError, match="日志分发失败"):
        await manager.stop()


@pytest.mark.asyncio
async def test_log_manager_rejects_unbounded_background_dispatch():
    manager = LogManager(run_id="run-1")
    manager._running = True
    entry = LogEntry(run_id="run-1", stream=LogStream.STDOUT, content="line", seq=1)
    for _ in range(MAX_DISPATCH_QUEUE_SIZE):
        manager._on_log_entry(entry)

    with pytest.raises(RuntimeError, match="日志分发队列已满"):
        manager._on_log_entry(entry)
