"""
日志管理器

整合 streamer/spool/realtime/batch/archive，实现完整的日志管理。

新架构特点：
- 实时日志通过 Redis Stream 推送
- 归档日志通过 WAL + S3 实现高可靠
- 不依赖本地文件存储（除 WAL）

Requirements: 9.1, 9.7
"""

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from loguru import logger

from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import ArtifactRef, LogEntry
from antcode_worker.config import DATA_ROOT
from antcode_worker.logs.archive import ArchiveConfig, LogArchiver
from antcode_worker.logs.batch import BackpressureState, BatchConfig, BatchSender
from antcode_worker.logs.realtime import RealtimeConfig, RealtimeSender
from antcode_worker.logs.spool import LogSpool, SpoolConfig
from antcode_worker.logs.streamer import LogStreamer


_DEFAULT_WAL_DIR = str(DATA_ROOT / "logs" / "wal")


class TransportProtocol(Protocol):
    """传输层协议"""

    async def send_log(self, log: Any) -> bool:
        """发送日志"""
        ...

    async def send_log_batch(self, logs: list[Any]) -> bool:
        """批量发送日志"""
        ...

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        ...


class DropPolicy(str, Enum):
    """日志丢弃策略"""
    NONE = "none"                # 不丢弃（阻塞）
    OLDEST = "oldest"            # 丢弃最旧的
    NEWEST = "newest"            # 丢弃最新的
    LOW_PRIORITY = "low_priority"  # 丢弃低优先级


@dataclass
class LogManagerConfig:
    """日志管理器配置"""
    
    # 功能开关
    enable_realtime: bool = True
    enable_batch: bool = True
    enable_spool: bool = True
    enable_archive: bool = True
    
    # 子模块配置
    spool_config: SpoolConfig = field(default_factory=SpoolConfig)
    realtime_config: RealtimeConfig = field(default_factory=RealtimeConfig)
    batch_config: BatchConfig = field(default_factory=BatchConfig)
    archive_config: ArchiveConfig = field(default_factory=ArchiveConfig)
    
    # WAL 目录（用于归档）
    wal_dir: str = _DEFAULT_WAL_DIR
    
    # 丢弃策略
    drop_policy: DropPolicy = DropPolicy.LOW_PRIORITY
    
    # 优先级（system > stderr > stdout）
    priority_order: list[str] = field(
        default_factory=lambda: ["system", "stderr", "stdout"]
    )


class LogManager:
    """
    日志管理器
    
    整合所有日志组件，提供统一的日志管理接口。
    
    功能：
    - 实时捕获 stdout/stderr
    - 实时发送到 Redis Stream
    - WAL 持久化（高可靠）
    - 归档到 S3
    - Backpressure 控制
    
    Requirements: 9.1, 9.7
    """

    def __init__(
        self,
        run_id: str,
        transport: TransportProtocol | None = None,
        config: LogManagerConfig | None = None,
        on_backpressure: Callable[[BackpressureState], None] | None = None,
        on_log_dropped: Callable[[LogEntry, str], None] | None = None,
    ):
        """
        初始化日志管理器
        
        Args:
            run_id: 运行 ID
            transport: 传输层实例
            config: 管理器配置
            on_backpressure: Backpressure 回调
            on_log_dropped: 日志丢弃回调
        """
        self.run_id = run_id
        self._transport = transport
        self._config = config or LogManagerConfig()
        self._on_backpressure = on_backpressure
        self._on_log_dropped = on_log_dropped
        
        # 子组件
        self._streamer: LogStreamer | None = None
        self._spool: LogSpool | None = None
        self._realtime: RealtimeSender | None = None
        self._batch: BatchSender | None = None
        self._archiver: LogArchiver | None = None
        
        # 状态
        self._running = False
        self._backpressure_state = BackpressureState.NORMAL
        self._dispatch_tasks: set[asyncio.Task] = set()
        
        # 统计
        self._total_entries = 0
        self._total_dropped = 0
        self._stdout_lines = 0
        self._stderr_lines = 0

    @property
    def backpressure_state(self) -> BackpressureState:
        """当前 backpressure 状态"""
        return self._backpressure_state

    @property
    def is_running(self) -> bool:
        """是否运行中"""
        return self._running

    async def start(self) -> None:
        """启动日志管理器"""
        if self._running:
            return
        
        self._running = True
        
        # 初始化子组件
        await self._init_components()
        
        logger.info(f"[{self.run_id}] 日志管理器已启动")

    async def stop(self) -> None:
        """停止日志管理器"""
        if not self._running:
            return
        
        self._running = False
        
        # 停止 streamer
        if self._streamer:
            await self._streamer.stop()

        await self._wait_dispatch_tasks()
        
        # 刷新并停止 batch
        if self._batch:
            await self._batch.flush()
            await self._batch.stop()
        
        # 刷新并停止 realtime
        if self._realtime:
            await self._realtime.stop()
        
        # 停止 spool
        if self._spool:
            await self._spool.mark_completed()
            await self._spool.stop()
        
        # 停止归档器
        if self._archiver:
            await self._archiver.stop()
        
        logger.info(f"[{self.run_id}] 日志管理器已停止")

    async def _init_components(self) -> None:
        """初始化子组件"""
        # Spool（用于断线恢复）
        if self._config.enable_spool:
            self._spool = LogSpool(
                run_id=self.run_id,
                config=self._config.spool_config,
            )
            await self._spool.start()
        
        # Realtime（实时发送到 Redis Stream）
        if self._config.enable_realtime and self._transport:
            self._realtime = RealtimeSender(
                run_id=self.run_id,
                transport=self._transport,
                config=self._config.realtime_config,
                on_send_failure=self._handle_realtime_failure,
            )
            await self._realtime.start()
        
        # Batch（批量发送）
        if self._config.enable_batch and self._transport:
            self._batch = BatchSender(
                run_id=self.run_id,
                transport=self._transport,
                config=self._config.batch_config,
                on_backpressure=self._handle_backpressure,
            )
            await self._batch.start()
        
        # Archiver（WAL + S3 归档）
        if self._config.enable_archive:
            archive_config = ArchiveConfig(
                wal_dir=self._config.wal_dir,
            )
            self._archiver = LogArchiver(
                run_id=self.run_id,
                config=archive_config,
            )
            await self._archiver.start()
        
        # Streamer（捕获进程输出）
        sinks = []
        if self._spool:
            sinks.append(self._spool)
        
        self._streamer = LogStreamer(
            run_id=self.run_id,
            sinks=sinks,
            on_entry=self._on_log_entry,
        )

    def _on_log_entry(self, entry: LogEntry) -> None:
        """日志条目回调"""
        if not self._running:
            return
        self._total_entries += 1
        
        if entry.stream == LogStream.STDOUT:
            self._stdout_lines += 1
        elif entry.stream == LogStream.STDERR:
            self._stderr_lines += 1
        
        # 异步分发
        task = asyncio.create_task(self._dispatch_entry(entry))
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch_entry(self, entry: LogEntry) -> None:
        """分发日志条目"""
        # 检查 backpressure
        if self._should_drop(entry):
            self._total_dropped += 1
            if self._on_log_dropped:
                self._on_log_dropped(entry, "backpressure")
            return
        
        # 写入 WAL（高可靠归档）
        if self._archiver:
            await self._archiver.write(
                log_type=entry.stream.value,
                content=entry.content,
                level="ERROR" if entry.stream == LogStream.STDERR else "INFO",
            )
        
        # 发送到 realtime（Redis Stream）
        if self._realtime:
            await self._realtime.write(entry)
        
        # 发送到 batch
        if self._batch:
            await self._batch.write(entry)

    async def _wait_dispatch_tasks(self) -> None:
        """等待已创建的分发任务完成"""
        if not self._dispatch_tasks:
            return
        tasks = list(self._dispatch_tasks)
        self._dispatch_tasks.clear()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)

    def _should_drop(self, entry: LogEntry) -> bool:
        """判断是否应该丢弃日志"""
        if self._config.drop_policy == DropPolicy.NONE:
            return False
        
        if self._backpressure_state not in (
            BackpressureState.CRITICAL,
            BackpressureState.BLOCKED,
        ):
            return False
        
        if self._config.drop_policy == DropPolicy.LOW_PRIORITY:
            stream_name = entry.stream.value
            priority = self._config.priority_order.index(stream_name) \
                if stream_name in self._config.priority_order else 999
            return priority >= len(self._config.priority_order) - 1
        
        return True

    def _handle_backpressure(self, state: BackpressureState) -> None:
        """处理 backpressure 状态变更"""
        self._backpressure_state = state
        if self._on_backpressure:
            self._on_backpressure(state)

    def _handle_realtime_failure(self, entry: LogEntry, error: str) -> None:
        """处理实时发送失败"""
        logger.debug(f"[{self.run_id}] 实时发送失败: {error}")

    async def capture_process(
        self,
        stdout: asyncio.StreamReader,
        stderr: asyncio.StreamReader,
    ) -> None:
        """
        捕获进程输出
        
        Args:
            stdout: stdout 流
            stderr: stderr 流
        """
        if self._streamer:
            await self._streamer.capture_both(stdout, stderr)

    async def write(self, entry: LogEntry) -> None:
        """
        写入日志条目（LogSink 协议）

        Args:
            entry: 日志条目
        """
        if not self._running:
            return

        # 写入 spool（断线恢复）
        if self._spool:
            await self._spool.write(entry)

        # 更新统计
        self._total_entries += 1
        if entry.stream == LogStream.STDOUT:
            self._stdout_lines += 1
        elif entry.stream == LogStream.STDERR:
            self._stderr_lines += 1

        # 分发
        await self._dispatch_entry(entry)

    async def write_log(
        self,
        content: str,
        stream: LogStream = LogStream.STDOUT,
        level: str = "INFO",
    ) -> None:
        """
        手动写入日志
        
        Args:
            content: 日志内容
            stream: 流类型
            level: 日志级别
        """
        if self._streamer:
            await self._streamer.write_system_log(content, level)

    async def flush(self) -> None:
        """刷新所有缓冲"""
        if self._streamer:
            await self._streamer.flush()
        
        if self._batch:
            await self._batch.flush()
        
        if self._spool:
            await self._spool.flush()

    async def archive_logs(self) -> list[ArtifactRef]:
        """
        归档日志到 S3
        
        Returns:
            归档产物列表
        """
        if not self._archiver:
            logger.debug(f"[{self.run_id}] 归档跳过: archiver 未启用")
            return []
        
        # 先刷新
        await self.flush()
        
        # 执行归档
        logger.info(f"[{self.run_id}] 开始归档日志到 S3...")
        results = await self._archiver.archive()
        
        artifacts = []
        for result in results:
            if result.success and result.artifact:
                artifacts.append(result.artifact)
                logger.info(
                    f"[{self.run_id}] 归档成功: {result.artifact.name}, "
                    f"size={result.compressed_size}, uri={result.s3_uri}"
                )
            else:
                logger.warning(f"[{self.run_id}] 归档失败: {result.error}")
        
        return artifacts

    async def recover_from_spool(self) -> int:
        """
        从 spool 恢复未发送的日志

        Returns:
            恢复的日志条目数
        """
        if not self._spool:
            return 0

        count = 0
        async for entry in self._spool.iter_unacked():
            if self._batch:
                await self._batch.write(entry)
            elif self._realtime:
                await self._realtime.write(entry)
            count += 1

        if count > 0:
            logger.info(f"[{self.run_id}] 从 spool 恢复了 {count} 条日志")
        return count

    async def ack_logs(self, seq: int) -> None:
        """
        确认已发送的日志

        Args:
            seq: 已确认的序列号
        """
        if self._spool:
            await self._spool.ack(seq)

    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = {
            "run_id": self.run_id,
            "running": self._running,
            "backpressure_state": self._backpressure_state.value,
            "total_entries": self._total_entries,
            "total_dropped": self._total_dropped,
            "stdout_lines": self._stdout_lines,
            "stderr_lines": self._stderr_lines,
        }

        if self._streamer:
            stats["streamer"] = self._streamer.get_stats()

        if self._spool:
            stats["spool"] = self._spool.get_stats()

        if self._realtime:
            stats["realtime"] = self._realtime.get_stats()

        if self._batch:
            stats["batch"] = self._batch.get_stats()

        if self._archiver:
            stats["archive"] = self._archiver.get_stats()

        return stats


class LogManagerFactory:
    """日志管理器工厂"""

    def __init__(
        self,
        transport: TransportProtocol | None = None,
        config: LogManagerConfig | None = None,
    ):
        self._transport = transport
        self._config = config or LogManagerConfig()

    def create(self, run_id: str) -> LogManager:
        """创建日志管理器实例"""
        return LogManager(
            run_id=run_id,
            transport=self._transport,
            config=self._config,
        )
