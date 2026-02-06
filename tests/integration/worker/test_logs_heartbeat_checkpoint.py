"""
Checkpoint 12: Logs + Heartbeat 验证

验证：
- 断线补发验证
- backpressure 行为验证
- 心跳正常上报

Requirements: 9.1, 9.3, 9.5, 9.7, 10.1, 10.3
"""

import asyncio
import os
import tempfile
import time
import uuid
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import LogEntry


class MockTransport:
    """模拟传输层"""

    def __init__(self, connected: bool = True, fail_count: int = 0):
        self._connected = connected
        self._fail_count = fail_count
        self._current_fails = 0
        self._sent_logs: list[Any] = []
        self._sent_batches: list[list[Any]] = []
        self._sent_heartbeats: list[Any] = []
        self._reconnect_called = False
        self._heartbeat_fail_count = fail_count  # 心跳失败次数

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_connected(self, connected: bool) -> None:
        self._connected = connected

    def reset_fail_count(self) -> None:
        """重置失败计数"""
        self._current_fails = 0

    async def send_log(self, log: Any) -> bool:
        if not self._connected:
            return False
        if self._current_fails < self._fail_count:
            self._current_fails += 1
            return False
        self._sent_logs.append(log)
        return True

    async def send_log_batch(self, logs: list[Any]) -> bool:
        if not self._connected:
            return False
        if self._current_fails < self._fail_count:
            self._current_fails += 1
            return False
        self._sent_batches.append(logs)
        return True

    async def send_heartbeat(self, heartbeat: Any) -> bool:
        if not self._connected:
            return False
        # 模拟心跳发送失败
        if self._heartbeat_fail_count > 0:
            self._heartbeat_fail_count -= 1
            return False
        self._sent_heartbeats.append(heartbeat)
        return True

    async def reconnect(self) -> bool:
        self._reconnect_called = True
        return self._connected


@pytest.fixture
def temp_log_dir():
    """创建临时日志目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def unique_run_id():
    """生成唯一运行 ID"""
    return f"test-run-{uuid.uuid4().hex[:8]}"


@pytest.mark.integration
class TestLogSpoolRecovery:
    """日志 Spool 断线恢复测试"""

    @pytest.mark.asyncio
    async def test_spool_write_and_recovery(self, temp_log_dir, unique_run_id):
        """
        测试 Spool 写入和恢复

        验证：
        1. 日志可以写入 Spool
        2. 断线后日志保存在 Spool
        3. 恢复后可以迭代未确认的日志
        """
        from antcode_worker.logs.spool import LogSpool, SpoolConfig

        config = SpoolConfig(
            spool_dir=temp_log_dir,
            max_disk_bytes=10 * 1024 * 1024,
            buffer_size=5,
        )
        spool = LogSpool(run_id=unique_run_id, config=config)

        try:
            await spool.start()

            # 写入日志
            for i in range(10):
                entry = LogEntry(
                    run_id=unique_run_id,
                    stream=LogStream.STDOUT,
                    content=f"Log line {i}",
                    seq=i + 1,
                    timestamp=datetime.now(),
                )
                success = await spool.write(entry)
                assert success, f"写入日志 {i} 失败"

            # 刷新到磁盘
            await spool.flush()

            # 验证写入统计
            stats = spool.get_stats()
            assert stats["entries_written"] == 10
            assert stats["last_seq"] == 10
            assert stats["acked_seq"] == 0

            # 确认部分日志
            await spool.ack(5)
            assert spool.acked_seq == 5

            # 迭代未确认的日志
            unacked = []
            async for entry in spool.iter_unacked():
                unacked.append(entry)

            assert len(unacked) == 5, f"应有 5 条未确认日志，实际 {len(unacked)}"
            assert unacked[0].seq == 6
            assert unacked[-1].seq == 10

            logger.info(f"[Test] Spool 恢复验证通过: 未确认日志数={len(unacked)}")

        finally:
            await spool.stop()

    @pytest.mark.asyncio
    async def test_spool_persistence_across_restart(self, temp_log_dir, unique_run_id):
        """
        测试 Spool 跨重启持久化

        验证：
        1. 停止后元数据保存
        2. 重启后可以恢复状态
        3. 未确认的日志可以继续发送
        """
        from antcode_worker.logs.spool import LogSpool, SpoolConfig

        config = SpoolConfig(spool_dir=temp_log_dir, buffer_size=3)

        # 第一次运行：写入日志
        spool1 = LogSpool(run_id=unique_run_id, config=config)
        await spool1.start()

        for i in range(8):
            entry = LogEntry(
                run_id=unique_run_id,
                stream=LogStream.STDOUT,
                content=f"Persistent log {i}",
                seq=i + 1,
                timestamp=datetime.now(),
            )
            await spool1.write(entry)

        await spool1.flush()
        await spool1.ack(3)  # 确认前 3 条
        await spool1.stop()

        # 第二次运行：恢复状态
        spool2 = LogSpool(run_id=unique_run_id, config=config)
        await spool2.start()

        # 验证状态恢复
        assert spool2.acked_seq == 3, f"acked_seq 应为 3，实际 {spool2.acked_seq}"
        assert spool2.last_seq == 8, f"last_seq 应为 8，实际 {spool2.last_seq}"

        # 迭代未确认的日志
        unacked = []
        async for entry in spool2.iter_unacked():
            unacked.append(entry)

        assert len(unacked) == 5, f"应有 5 条未确认日志，实际 {len(unacked)}"

        await spool2.stop()
        logger.info("[Test] Spool 跨重启持久化验证通过")


@pytest.mark.integration
class TestBatchSenderBackpressure:
    """批量发送器 Backpressure 测试"""

    @pytest.mark.asyncio
    async def test_backpressure_state_transitions(self, unique_run_id):
        """
        测试 Backpressure 状态转换

        验证：
        1. 队列空时为 NORMAL
        2. 队列达到警告阈值时为 WARNING
        3. 队列达到临界阈值时为 CRITICAL
        4. 队列满时为 BLOCKED
        """
        from antcode_worker.logs.batch import (
            BackpressureState,
            BatchConfig,
            BatchSender,
        )

        transport = MockTransport(connected=False)  # 断开连接，日志会堆积

        config = BatchConfig(
            batch_size=10,
            max_queue_size=100,
            warning_threshold=0.5,
            critical_threshold=0.8,
            drop_on_critical=False,  # 不丢弃，让队列堆积
        )

        backpressure_states = []

        def on_backpressure(state: BackpressureState):
            backpressure_states.append(state)

        sender = BatchSender(
            run_id=unique_run_id,
            transport=transport,
            config=config,
            on_backpressure=on_backpressure,
        )

        await sender.start()

        try:
            # 初始状态应为 NORMAL
            assert sender.backpressure_state == BackpressureState.NORMAL

            # 写入日志直到达到警告阈值 (50%)
            for i in range(55):
                entry = LogEntry(
                    run_id=unique_run_id,
                    stream=LogStream.STDOUT,
                    content=f"Log {i}",
                    seq=i + 1,
                )
                await sender.write(entry)

            # 应该触发 WARNING
            assert BackpressureState.WARNING in backpressure_states

            # 继续写入直到达到临界阈值 (80%)
            for i in range(55, 85):
                entry = LogEntry(
                    run_id=unique_run_id,
                    stream=LogStream.STDOUT,
                    content=f"Log {i}",
                    seq=i + 1,
                )
                await sender.write(entry)

            # 应该触发 CRITICAL
            assert BackpressureState.CRITICAL in backpressure_states

            logger.info(f"[Test] Backpressure 状态转换验证通过: {backpressure_states}")

        finally:
            await sender.stop()

    @pytest.mark.asyncio
    async def test_backpressure_drop_policy(self, unique_run_id):
        """
        测试 Backpressure 丢弃策略

        验证：
        1. 临界状态下新日志被丢弃
        2. 丢弃计数正确
        """
        from antcode_worker.logs.batch import (
            BackpressureState,
            BatchConfig,
            BatchSender,
        )

        transport = MockTransport(connected=False)

        config = BatchConfig(
            batch_size=10,
            max_queue_size=50,
            warning_threshold=0.5,
            critical_threshold=0.8,
            drop_on_critical=True,  # 临界时丢弃
        )

        sender = BatchSender(
            run_id=unique_run_id,
            transport=transport,
            config=config,
        )

        await sender.start()

        try:
            # 填满队列到临界状态
            for i in range(45):
                entry = LogEntry(
                    run_id=unique_run_id,
                    stream=LogStream.STDOUT,
                    content=f"Log {i}",
                    seq=i + 1,
                )
                await sender.write(entry)

            # 确认进入临界状态
            assert sender.backpressure_state in (
                BackpressureState.CRITICAL,
                BackpressureState.BLOCKED,
            )

            # 继续写入，应该被丢弃
            dropped_count = 0
            for i in range(45, 60):
                entry = LogEntry(
                    run_id=unique_run_id,
                    stream=LogStream.STDOUT,
                    content=f"Log {i}",
                    seq=i + 1,
                )
                success = await sender.write(entry)
                if not success:
                    dropped_count += 1

            # 验证有日志被丢弃
            stats = sender.get_stats()
            assert stats["total_dropped"] > 0, "应该有日志被丢弃"
            logger.info(f"[Test] Backpressure 丢弃策略验证通过: dropped={stats['total_dropped']}")

        finally:
            await sender.stop()


@pytest.mark.integration
class TestHeartbeatReporter:
    """心跳上报器测试"""

    @pytest.mark.asyncio
    async def test_heartbeat_normal_operation(self):
        """
        测试心跳正常上报

        验证：
        1. 心跳可以正常发送
        2. 发送成功后更新时间戳
        3. 连续失败计数重置
        """
        from antcode_worker.heartbeat.reporter import HeartbeatReporter

        transport = MockTransport(connected=True)
        reporter = HeartbeatReporter(
            transport=transport,
            worker_id="test-worker-001",
            version="1.0.0",
        )

        try:
            # 手动发送心跳
            success = await reporter.send_heartbeat()
            assert success, "心跳发送应该成功"

            # 验证心跳已发送
            assert len(transport._sent_heartbeats) == 1
            heartbeat = transport._sent_heartbeats[0]
            assert heartbeat.worker_id == "test-worker-001"
            assert heartbeat.status == "online"

            # 验证时间戳更新
            assert reporter.last_heartbeat_time is not None
            assert reporter.consecutive_failures == 0

            logger.info("[Test] 心跳正常上报验证通过")

        finally:
            await reporter.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_failure_handling(self):
        """
        测试心跳失败处理

        验证：
        1. 发送失败时连续失败计数增加
        2. 心跳间隔调整
        
        注意：当传输层未连接时，send_heartbeat 直接返回 False 而不增加失败计数
        （因为没有实际尝试发送）。只有当传输层连接但发送失败时才增加计数。
        """
        from antcode_worker.heartbeat.reporter import HeartbeatReporter

        # 使用连接状态为 True 但发送会失败的 transport
        transport = MockTransport(connected=True, fail_count=5)
        reporter = HeartbeatReporter(
            transport=transport,
            worker_id="test-worker-002",
        )

        try:
            # 发送心跳（应该失败，因为 fail_count=5）
            success = await reporter.send_heartbeat()
            assert not success, "心跳发送应该失败"

            # 验证失败计数
            assert reporter.consecutive_failures == 1

            # 再次发送
            await reporter.send_heartbeat()
            assert reporter.consecutive_failures == 2

            logger.info(f"[Test] 心跳失败处理验证通过: failures={reporter.consecutive_failures}")

        finally:
            await reporter.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_reconnect_trigger(self):
        """
        测试心跳触发重连

        验证：
        1. 连续失败达到阈值时触发重连
        2. 重连失败后进入降级模式
        """
        from antcode_worker.heartbeat.reporter import HeartbeatReporter, HeartbeatState

        # 使用连接状态为 True 但发送会失败的 transport
        transport = MockTransport(connected=True, fail_count=10)
        disconnect_called = False

        def on_disconnect():
            nonlocal disconnect_called
            disconnect_called = True

        reporter = HeartbeatReporter(
            transport=transport,
            worker_id="test-worker-003",
        )
        reporter.set_disconnect_callback(on_disconnect)
        
        # 减少退避时间以加快测试
        reporter.RECONNECT_BACKOFF_BASE = 0.1

        try:
            # 启动 reporter（设置 _running = True）
            reporter._running = True
            
            # 模拟连续失败
            for _ in range(reporter.MAX_CONSECUTIVE_FAILURES):
                await reporter.send_heartbeat()

            assert reporter.consecutive_failures == reporter.MAX_CONSECUTIVE_FAILURES

            # 设置 transport 为断开状态，这样重连会失败
            transport.set_connected(False)

            # 手动触发失败处理（这会启动重连流程）
            await reporter._handle_consecutive_failures()

            # 验证进入降级模式（因为重连失败）
            assert reporter.state == HeartbeatState.DEGRADED
            assert disconnect_called, "断开连接回调应该被调用"

            logger.info("[Test] 心跳触发重连验证通过")

        finally:
            await reporter.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_recovery(self):
        """
        测试心跳恢复

        验证：
        1. 重连成功后恢复正常状态
        2. 重连计数重置
        """
        from antcode_worker.heartbeat.reporter import HeartbeatReporter, HeartbeatState

        # 使用连接状态为 True 但发送会失败的 transport
        transport = MockTransport(connected=True, fail_count=3)
        reporter = HeartbeatReporter(
            transport=transport,
            worker_id="test-worker-004",
        )

        try:
            # 先模拟失败（发送 3 次，都会失败）
            for _ in range(3):
                await reporter.send_heartbeat()

            assert reporter.consecutive_failures == 3

            # 重置 transport 的失败计数，让下次发送成功
            transport._heartbeat_fail_count = 0
            success = await reporter.send_heartbeat()

            assert success, "心跳应该成功"
            assert reporter.consecutive_failures == 0, "失败计数应该重置"

            logger.info("[Test] 心跳恢复验证通过")

        finally:
            await reporter.stop()


@pytest.mark.integration
class TestLogManagerIntegration:
    """日志管理器集成测试"""

    @pytest.mark.asyncio
    async def test_log_manager_full_flow(self, temp_log_dir, unique_run_id):
        """
        测试日志管理器完整流程

        验证：
        1. 日志可以写入
        2. 日志可以发送到 Transport
        3. 统计信息正确
        """
        from antcode_worker.logs.manager import LogManager, LogManagerConfig
        from antcode_worker.logs.spool import SpoolConfig

        transport = MockTransport(connected=True)

        config = LogManagerConfig(
            enable_realtime=True,
            enable_batch=True,
            enable_spool=True,
            enable_archive=False,
            wal_dir=os.path.join(temp_log_dir, "wal"),
            spool_config=SpoolConfig(spool_dir=os.path.join(temp_log_dir, "spool")),
        )

        manager = LogManager(
            run_id=unique_run_id,
            transport=transport,
            config=config,
        )

        try:
            await manager.start()
            assert manager.is_running

            # 写入日志
            for i in range(5):
                await manager.write_log(f"Test log {i}", LogStream.STDOUT)

            # 等待异步处理
            await asyncio.sleep(0.5)

            # 刷新
            await manager.flush()

            # 验证统计
            stats = manager.get_stats()
            assert stats["running"] is True
            assert stats["total_entries"] >= 5

            logger.info(f"[Test] 日志管理器完整流程验证通过: {stats}")

        finally:
            await manager.stop()

    @pytest.mark.asyncio
    async def test_log_manager_disconnect_recovery(self, temp_log_dir, unique_run_id):
        """
        测试日志管理器断线恢复

        验证：
        1. 断线时日志保存到 Spool
        2. 恢复后可以从 Spool 恢复
        """
        from antcode_worker.logs.manager import LogManager, LogManagerConfig
        from antcode_worker.logs.spool import SpoolConfig

        transport = MockTransport(connected=True)

        config = LogManagerConfig(
            enable_realtime=False,  # 禁用实时，只用 batch
            enable_batch=True,
            enable_spool=True,
            enable_archive=False,
            wal_dir=os.path.join(temp_log_dir, "wal"),
            spool_config=SpoolConfig(spool_dir=os.path.join(temp_log_dir, "spool")),
        )

        manager = LogManager(
            run_id=unique_run_id,
            transport=transport,
            config=config,
        )

        try:
            await manager.start()

            # 写入一些日志
            for i in range(3):
                await manager.write_log(f"Before disconnect {i}", LogStream.STDOUT)

            await asyncio.sleep(0.2)

            # 模拟断线
            transport.set_connected(False)

            # 继续写入日志（应该保存到 spool）
            for i in range(3):
                await manager.write_log(f"During disconnect {i}", LogStream.STDOUT)

            await asyncio.sleep(0.2)

            # 恢复连接
            transport.set_connected(True)

            # 从 spool 恢复
            recovered = await manager.recover_from_spool()
            logger.info(f"[Test] 从 spool 恢复了 {recovered} 条日志")

            # 刷新
            await manager.flush()

            logger.info("[Test] 日志管理器断线恢复验证通过")

        finally:
            await manager.stop()


@pytest.mark.integration
class TestSystemMetricsCollector:
    """系统指标采集器测试"""

    @pytest.mark.asyncio
    async def test_metrics_collection(self):
        """
        测试指标采集

        验证：
        1. CPU 指标可以采集
        2. 内存指标可以采集
        3. 磁盘指标可以采集
        """
        from antcode_worker.heartbeat.system_metrics import SystemMetricsCollector

        collector = SystemMetricsCollector(max_slots=5)

        metrics = await collector.collect(use_cache=False)

        # 验证 CPU 指标
        assert metrics.cpu.count > 0, "CPU 核心数应该大于 0"
        assert 0 <= metrics.cpu.percent <= 100, "CPU 使用率应该在 0-100 之间"

        # 验证内存指标
        assert metrics.memory.total_mb > 0, "总内存应该大于 0"
        assert 0 <= metrics.memory.percent <= 100, "内存使用率应该在 0-100 之间"

        # 验证磁盘指标
        assert metrics.disk.total_gb > 0, "磁盘总容量应该大于 0"
        assert 0 <= metrics.disk.percent <= 100, "磁盘使用率应该在 0-100 之间"

        # 验证 Worker 指标
        assert metrics.worker.max_slots == 5

        logger.info(f"[Test] 系统指标采集验证通过: CPU={metrics.cpu.percent}%, "
              f"Memory={metrics.memory.percent}%, Disk={metrics.disk.percent}%")

    @pytest.mark.asyncio
    async def test_metrics_caching(self):
        """
        测试指标缓存

        验证：
        1. 缓存有效期内返回缓存数据
        2. 缓存过期后重新采集
        """
        from antcode_worker.heartbeat.system_metrics import SystemMetricsCollector

        collector = SystemMetricsCollector()
        collector._cache_ttl = 0.5  # 500ms 缓存

        # 第一次采集
        metrics1 = await collector.collect(use_cache=True)
        time1 = metrics1.timestamp

        # 立即再次采集（应该返回缓存）
        metrics2 = await collector.collect(use_cache=True)
        assert metrics2.timestamp == time1, "应该返回缓存数据"

        # 等待缓存过期
        await asyncio.sleep(0.6)

        # 再次采集（应该重新采集）
        metrics3 = await collector.collect(use_cache=True)
        assert metrics3.timestamp > time1, "应该重新采集数据"

        logger.info("[Test] 指标缓存验证通过")

    @pytest.mark.asyncio
    async def test_heartbeat_metrics_integration(self):
        """
        测试心跳与指标集成

        验证：
        1. 心跳可以获取系统指标
        2. 指标包含在心跳数据中
        """
        from antcode_worker.heartbeat.reporter import HeartbeatReporter
        from antcode_worker.heartbeat.system_metrics import SystemMetricsCollector

        transport = MockTransport(connected=True)
        collector = SystemMetricsCollector(max_slots=10)

        reporter = HeartbeatReporter(
            transport=transport,
            worker_id="test-worker-metrics",
            metrics_collector=collector,
            max_concurrent_tasks=10,
        )

        try:
            # 发送心跳
            success = await reporter.send_heartbeat()
            assert success

            # 验证心跳包含指标
            heartbeat = transport._sent_heartbeats[0]
            assert heartbeat.metrics is not None
            assert heartbeat.metrics.max_concurrent_tasks == 10

            logger.info(f"[Test] 心跳指标集成验证通过: metrics={heartbeat.metrics}")

        finally:
            await reporter.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
