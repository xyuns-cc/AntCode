"""
日志本地缓冲（Spool）

负责将日志缓冲到本地磁盘，支持断线恢复。

Requirements: 9.3
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

import aiofiles
import aiofiles.os
from loguru import logger

from antcode_worker.domain.enums import LogStream
from antcode_worker.domain.models import LogEntry
from antcode_worker.config import DATA_ROOT


_DEFAULT_SPOOL_DIR = str(DATA_ROOT / "logs" / "spool")


@dataclass
class SpoolConfig:
    """Spool 配置"""
    
    # 存储路径
    spool_dir: str = _DEFAULT_SPOOL_DIR
    
    # 磁盘限制
    max_disk_bytes: int = 100 * 1024 * 1024  # 100MB
    max_file_bytes: int = 10 * 1024 * 1024   # 10MB per file
    
    # 清理策略
    retention_seconds: int = 3600 * 24       # 24 小时
    cleanup_interval: int = 300              # 5 分钟检查一次
    
    # 写入配置
    flush_interval: float = 1.0              # 刷新间隔
    buffer_size: int = 100                   # 内存缓冲条目数


@dataclass
class SpoolMeta:
    """Spool 元数据"""
    run_id: str
    created_at: float
    last_seq: int = 0
    acked_seq: int = 0
    file_count: int = 0
    total_bytes: int = 0
    completed: bool = False


class LogSpool:
    """
    日志本地缓冲
    
    将日志写入本地磁盘，支持：
    - 断线恢复：重连后从 acked_seq 继续发送
    - 磁盘限制：超过限制时丢弃旧日志
    - 自动清理：过期日志自动删除
    
    Requirements: 9.3
    """

    def __init__(
        self,
        run_id: str,
        config: SpoolConfig | None = None,
    ):
        """
        初始化日志 Spool
        
        Args:
            run_id: 运行 ID
            config: Spool 配置
        """
        self.run_id = run_id
        self._config = config or SpoolConfig()
        
        # 路径
        self._spool_path = Path(self._config.spool_dir) / run_id
        self._meta_file = self._spool_path / "meta.json"
        self._current_file: Path | None = None
        self._current_handle: BinaryIO | None = None
        
        # 元数据
        self._meta = SpoolMeta(
            run_id=run_id,
            created_at=time.time(),
        )
        
        # 内存缓冲
        self._buffer: list[LogEntry] = []
        self._buffer_lock = asyncio.Lock()
        
        # 状态
        self._running = False
        self._flush_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        
        # 统计
        self._bytes_written = 0
        self._entries_written = 0
        self._entries_dropped = 0

    @property
    def acked_seq(self) -> int:
        """已确认的序列号"""
        return self._meta.acked_seq

    @property
    def last_seq(self) -> int:
        """最后写入的序列号"""
        return self._meta.last_seq

    async def start(self) -> None:
        """启动 Spool"""
        if self._running:
            return
        
        self._running = True
        
        # 创建目录
        await aiofiles.os.makedirs(self._spool_path, exist_ok=True)
        
        # 加载或创建元数据
        await self._load_or_create_meta()
        
        # 打开当前文件
        await self._open_current_file()
        
        # 启动后台任务
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        logger.info(f"[{self.run_id}] Spool 已启动: {self._spool_path}")

    async def stop(self) -> None:
        """停止 Spool"""
        self._running = False
        
        # 停止后台任务
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # 刷新缓冲
        await self.flush()
        
        # 关闭文件
        await self._close_current_file()
        
        # 保存元数据
        await self._save_meta()
        
        logger.info(f"[{self.run_id}] Spool 已停止")

    async def write(self, entry: LogEntry) -> bool:
        """
        写入日志条目
        
        Args:
            entry: 日志条目
            
        Returns:
            是否成功写入
        """
        if not self._running:
            return False
        
        # 检查磁盘限制
        if self._bytes_written >= self._config.max_disk_bytes:
            self._entries_dropped += 1
            return False
        
        async with self._buffer_lock:
            self._buffer.append(entry)
            
            # 缓冲满时立即刷新
            if len(self._buffer) >= self._config.buffer_size:
                await self._flush_buffer()
        
        return True

    async def flush(self) -> None:
        """刷新缓冲到磁盘"""
        async with self._buffer_lock:
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """刷新内存缓冲到磁盘（需持有锁）"""
        if not self._buffer:
            return
        
        entries = self._buffer.copy()
        self._buffer.clear()
        
        for entry in entries:
            await self._write_entry(entry)
        
        # 同步文件
        if self._current_handle:
            try:
                await self._current_handle.flush()
            except Exception:
                pass

    async def _write_entry(self, entry: LogEntry) -> None:
        """写入单个条目到文件"""
        if not self._current_handle:
            await self._open_current_file()
        
        # 检查文件大小
        if self._current_file and self._current_file.exists():
            if self._current_file.stat().st_size >= self._config.max_file_bytes:
                await self._rotate_file()
        
        # 序列化
        data = {
            "run_id": entry.run_id,
            "stream": entry.stream.value,
            "content": entry.content,
            "seq": entry.seq,
            "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            "level": entry.level,
            "source": entry.source,
        }
        line = json.dumps(data, ensure_ascii=False) + "\n"
        line_bytes = line.encode("utf-8")
        
        try:
            await self._current_handle.write(line_bytes)
            self._bytes_written += len(line_bytes)
            self._entries_written += 1
            self._meta.last_seq = max(self._meta.last_seq, entry.seq)
            self._meta.total_bytes = self._bytes_written
        except Exception as e:
            logger.error(f"[{self.run_id}] 写入 spool 失败: {e}")

    async def _open_current_file(self) -> None:
        """打开当前写入文件"""
        file_index = self._meta.file_count
        self._current_file = self._spool_path / f"log_{file_index:04d}.jsonl"
        self._current_handle = await aiofiles.open(self._current_file, "ab")

    async def _close_current_file(self) -> None:
        """关闭当前文件"""
        if self._current_handle:
            try:
                await self._current_handle.close()
            except Exception:
                pass
            self._current_handle = None

    async def _rotate_file(self) -> None:
        """轮转文件"""
        await self._close_current_file()
        self._meta.file_count += 1
        await self._open_current_file()
        await self._save_meta()

    async def _load_or_create_meta(self) -> None:
        """加载或创建元数据"""
        if self._meta_file.exists():
            try:
                async with aiofiles.open(self._meta_file, "r") as f:
                    data = json.loads(await f.read())
                    self._meta = SpoolMeta(
                        run_id=data.get("run_id", self.run_id),
                        created_at=data.get("created_at", time.time()),
                        last_seq=data.get("last_seq", 0),
                        acked_seq=data.get("acked_seq", 0),
                        file_count=data.get("file_count", 0),
                        total_bytes=data.get("total_bytes", 0),
                        completed=data.get("completed", False),
                    )
                    self._bytes_written = self._meta.total_bytes
            except Exception as e:
                logger.warning(f"[{self.run_id}] 加载 spool 元数据失败: {e}")
        
        await self._save_meta()

    async def _save_meta(self) -> None:
        """保存元数据"""
        data = {
            "run_id": self._meta.run_id,
            "created_at": self._meta.created_at,
            "last_seq": self._meta.last_seq,
            "acked_seq": self._meta.acked_seq,
            "file_count": self._meta.file_count,
            "total_bytes": self._meta.total_bytes,
            "completed": self._meta.completed,
        }
        try:
            async with aiofiles.open(self._meta_file, "w") as f:
                await f.write(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"[{self.run_id}] 保存 spool 元数据失败: {e}")

    async def ack(self, seq: int) -> None:
        """
        确认已发送的序列号
        
        Args:
            seq: 已确认的序列号
        """
        if seq > self._meta.acked_seq:
            self._meta.acked_seq = seq
            await self._save_meta()

    async def iter_unacked(self) -> AsyncIterator[LogEntry]:
        """
        迭代未确认的日志条目
        
        用于断线恢复时重发日志。
        
        Yields:
            未确认的 LogEntry
        """
        # 遍历所有日志文件
        for i in range(self._meta.file_count + 1):
            log_file = self._spool_path / f"log_{i:04d}.jsonl"
            if not log_file.exists():
                continue
            
            try:
                async with aiofiles.open(log_file, "r") as f:
                    async for line in f:
                        if not line.strip():
                            continue
                        
                        try:
                            data = json.loads(line)
                            seq = data.get("seq", 0)
                            
                            # 跳过已确认的
                            if seq <= self._meta.acked_seq:
                                continue
                            
                            yield LogEntry(
                                run_id=data.get("run_id", self.run_id),
                                stream=LogStream(data.get("stream", "stdout")),
                                content=data.get("content", ""),
                                seq=seq,
                                timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else None,
                                level=data.get("level", "INFO"),
                                source=data.get("source"),
                            )
                        except Exception as e:
                            logger.warning(f"[{self.run_id}] 解析日志行失败: {e}")
            except Exception as e:
                logger.error(f"[{self.run_id}] 读取日志文件失败: {e}")

    async def mark_completed(self) -> None:
        """标记为已完成"""
        self._meta.completed = True
        await self._save_meta()

    async def _flush_loop(self) -> None:
        """定期刷新循环"""
        while self._running:
            try:
                await asyncio.sleep(self._config.flush_interval)
                if not self._running:
                    break
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.run_id}] Spool 刷新异常: {e}")

    async def _cleanup_loop(self) -> None:
        """定期清理循环"""
        while self._running:
            try:
                await asyncio.sleep(self._config.cleanup_interval)
                if not self._running:
                    break
                await self._cleanup_old_spools()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.run_id}] Spool 清理异常: {e}")

    async def _cleanup_old_spools(self) -> None:
        """清理过期的 spool"""
        spool_base = Path(self._config.spool_dir)
        if not spool_base.exists():
            return
        
        now = time.time()
        
        for run_dir in spool_base.iterdir():
            if not run_dir.is_dir():
                continue
            
            # 跳过当前运行
            if run_dir.name == self.run_id:
                continue
            
            meta_file = run_dir / "meta.json"
            if not meta_file.exists():
                continue
            
            try:
                async with aiofiles.open(meta_file, "r") as f:
                    data = json.loads(await f.read())
                    created_at = data.get("created_at", 0)
                    completed = data.get("completed", False)
                    
                    # 已完成且过期的可以删除
                    if completed and (now - created_at) > self._config.retention_seconds:
                        await self._remove_spool_dir(run_dir)
            except Exception as e:
                logger.warning(f"清理 spool 时出错: {e}")

    async def _remove_spool_dir(self, path: Path) -> None:
        """删除 spool 目录"""
        try:
            import shutil
            shutil.rmtree(path)
            logger.info(f"已清理过期 spool: {path}")
        except Exception as e:
            logger.error(f"删除 spool 目录失败: {e}")

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "run_id": self.run_id,
            "spool_path": str(self._spool_path),
            "bytes_written": self._bytes_written,
            "entries_written": self._entries_written,
            "entries_dropped": self._entries_dropped,
            "last_seq": self._meta.last_seq,
            "acked_seq": self._meta.acked_seq,
            "file_count": self._meta.file_count,
            "completed": self._meta.completed,
            "running": self._running,
        }
