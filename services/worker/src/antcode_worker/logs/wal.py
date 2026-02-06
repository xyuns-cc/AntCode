"""
Write-Ahead Log (WAL) 模块

提供高可靠的日志持久化，确保日志不丢失。

特点：
- Append-only 写入，fsync 保证持久化
- 支持崩溃恢复
- 支持日志轮转
- 确认删除机制
"""

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import AsyncIterator

import aiofiles
import aiofiles.os
from loguru import logger


class WALState(str, Enum):
    """WAL 状态"""
    ACTIVE = "active"           # 正在写入
    SEALED = "sealed"           # 已封存，等待上传
    UPLOADING = "uploading"     # 上传中
    UPLOADED = "uploaded"       # 已上传，等待确认删除
    COMPLETED = "completed"     # 已完成


@dataclass
class WALConfig:
    """WAL 配置"""
    wal_dir: str = "var/worker/logs/wal"
    max_file_size: int = 10 * 1024 * 1024   # 10MB 触发轮转
    sync_interval: float = 1.0               # fsync 间隔（秒）
    sync_on_write: bool = False              # 每次写入都 fsync（更可靠但慢）
    retention_hours: int = 72                # WAL 保留时间（小时）


@dataclass
class WALEntry:
    """WAL 条目"""
    seq: int
    timestamp: float
    log_type: str       # stdout/stderr/system
    content: str
    level: str = "INFO"
    
    def to_line(self) -> str:
        """序列化为一行"""
        data = {
            "seq": self.seq,
            "ts": self.timestamp,
            "type": self.log_type,
            "content": self.content,
            "level": self.level,
        }
        return json.dumps(data, ensure_ascii=False) + "\n"
    
    @classmethod
    def from_line(cls, line: str) -> "WALEntry":
        """从一行反序列化"""
        data = json.loads(line.strip())
        return cls(
            seq=data["seq"],
            timestamp=data["ts"],
            log_type=data["type"],
            content=data["content"],
            level=data.get("level", "INFO"),
        )


@dataclass
class WALMetadata:
    """WAL 元数据"""
    run_id: str
    state: WALState = WALState.ACTIVE
    created_at: float = field(default_factory=time.time)
    sealed_at: float | None = None
    uploaded_at: float | None = None
    entry_count: int = 0
    byte_size: int = 0
    checksum: str = ""
    s3_uri: str = ""
    
    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "sealed_at": self.sealed_at,
            "uploaded_at": self.uploaded_at,
            "entry_count": self.entry_count,
            "byte_size": self.byte_size,
            "checksum": self.checksum,
            "s3_uri": self.s3_uri,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "WALMetadata":
        return cls(
            run_id=data["run_id"],
            state=WALState(data["state"]),
            created_at=data.get("created_at", time.time()),
            sealed_at=data.get("sealed_at"),
            uploaded_at=data.get("uploaded_at"),
            entry_count=data.get("entry_count", 0),
            byte_size=data.get("byte_size", 0),
            checksum=data.get("checksum", ""),
            s3_uri=data.get("s3_uri", ""),
        )


class WALWriter:
    """
    WAL 写入器
    
    负责将日志写入本地 WAL 文件，保证持久化。
    """

    def __init__(self, run_id: str, config: WALConfig | None = None):
        self.run_id = run_id
        self._config = config or WALConfig()
        
        # 文件路径
        self._wal_dir = Path(self._config.wal_dir) / run_id
        self._wal_file = self._wal_dir / "log.wal"
        self._meta_file = self._wal_dir / "meta.json"
        
        # 状态
        self._file_handle = None
        self._metadata: WALMetadata | None = None
        self._seq = 0
        self._byte_count = 0
        self._hasher = hashlib.sha256()
        
        # 同步控制
        self._sync_task: asyncio.Task | None = None
        self._dirty = False
        self._lock = asyncio.Lock()
        self._running = False

    @property
    def metadata(self) -> WALMetadata | None:
        return self._metadata

    @property
    def wal_path(self) -> Path:
        return self._wal_file

    async def start(self) -> None:
        """启动 WAL 写入器"""
        if self._running:
            return
        
        self._running = True
        
        # 创建目录
        self._wal_dir.mkdir(parents=True, exist_ok=True)
        
        # 检查是否有未完成的 WAL
        if self._meta_file.exists():
            await self._recover()
        else:
            # 创建新的 WAL
            self._metadata = WALMetadata(run_id=self.run_id)
            await self._save_metadata()
        
        # 打开文件（追加模式）
        self._file_handle = await aiofiles.open(self._wal_file, "a")
        
        # 启动定时同步
        if not self._config.sync_on_write:
            self._sync_task = asyncio.create_task(self._sync_loop())
        
        logger.debug(f"[{self.run_id}] WAL 写入器已启动: {self._wal_file}")

    async def stop(self) -> None:
        """停止 WAL 写入器"""
        if not self._running:
            return
        
        self._running = False
        
        # 停止同步任务
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        
        # 最终同步
        await self._sync()
        
        # 关闭文件
        if self._file_handle:
            await self._file_handle.close()
            self._file_handle = None
        
        logger.debug(f"[{self.run_id}] WAL 写入器已停止")

    async def write(self, log_type: str, content: str, level: str = "INFO") -> int:
        """
        写入日志条目
        
        Args:
            log_type: 日志类型 (stdout/stderr/system)
            content: 日志内容
            level: 日志级别
            
        Returns:
            序列号
        """
        if not self._running or not self._file_handle:
            return -1
        
        async with self._lock:
            self._seq += 1
            entry = WALEntry(
                seq=self._seq,
                timestamp=time.time(),
                log_type=log_type,
                content=content,
                level=level,
            )
            
            line = entry.to_line()
            line_bytes = line.encode("utf-8")
            
            await self._file_handle.write(line)
            self._hasher.update(line_bytes)
            self._byte_count += len(line_bytes)
            self._dirty = True
            
            if self._metadata:
                self._metadata.entry_count = self._seq
                self._metadata.byte_size = self._byte_count
            
            # 同步写入模式
            if self._config.sync_on_write:
                await self._sync()
            
            return self._seq

    async def seal(self) -> WALMetadata:
        """
        封存 WAL，准备上传
        
        Returns:
            WAL 元数据
        """
        async with self._lock:
            # 最终同步
            await self._sync()
            
            # 更新元数据
            if self._metadata:
                self._metadata.state = WALState.SEALED
                self._metadata.sealed_at = time.time()
                self._metadata.checksum = self._hasher.hexdigest()
                await self._save_metadata()
            
            logger.info(
                f"[{self.run_id}] WAL 已封存: "
                f"entries={self._seq}, bytes={self._byte_count}, "
                f"checksum={self._metadata.checksum[:16] if self._metadata else ''}..."
            )
            
            return self._metadata

    async def mark_uploaded(self, s3_uri: str) -> None:
        """标记为已上传"""
        if self._metadata:
            self._metadata.state = WALState.UPLOADED
            self._metadata.uploaded_at = time.time()
            self._metadata.s3_uri = s3_uri
            await self._save_metadata()
            logger.info(f"[{self.run_id}] WAL 已上传: {s3_uri}")

    async def mark_completed(self) -> None:
        """标记为已完成，可以删除"""
        if self._metadata:
            self._metadata.state = WALState.COMPLETED
            await self._save_metadata()

    async def delete(self) -> bool:
        """删除 WAL 文件"""
        try:
            if self._wal_file.exists():
                await aiofiles.os.remove(self._wal_file)
            if self._meta_file.exists():
                await aiofiles.os.remove(self._meta_file)
            if self._wal_dir.exists():
                await aiofiles.os.rmdir(self._wal_dir)
            logger.debug(f"[{self.run_id}] WAL 已删除")
            return True
        except Exception as e:
            logger.error(f"[{self.run_id}] 删除 WAL 失败: {e}")
            return False

    async def _recover(self) -> None:
        """从已有 WAL 恢复"""
        try:
            async with aiofiles.open(self._meta_file, "r") as f:
                data = json.loads(await f.read())
                self._metadata = WALMetadata.from_dict(data)
            
            # 重新计算状态
            if self._wal_file.exists():
                stat = await aiofiles.os.stat(self._wal_file)
                self._byte_count = stat.st_size
                
                # 重新计算 checksum 和 seq
                async with aiofiles.open(self._wal_file, "r") as f:
                    async for line in f:
                        if line.strip():
                            self._hasher.update(line.encode("utf-8"))
                            try:
                                entry = WALEntry.from_line(line)
                                self._seq = max(self._seq, entry.seq)
                            except Exception:
                                pass
            
            logger.info(f"[{self.run_id}] WAL 已恢复: state={self._metadata.state.value}, seq={self._seq}")
            
        except Exception as e:
            logger.warning(f"[{self.run_id}] WAL 恢复失败，创建新的: {e}")
            self._metadata = WALMetadata(run_id=self.run_id)
            await self._save_metadata()

    async def _sync(self) -> None:
        """同步到磁盘"""
        if not self._dirty or not self._file_handle:
            return
        
        await self._file_handle.flush()
        os.fsync(self._file_handle.fileno())
        self._dirty = False

    async def _sync_loop(self) -> None:
        """定时同步循环"""
        while self._running:
            try:
                await asyncio.sleep(self._config.sync_interval)
                async with self._lock:
                    await self._sync()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.run_id}] WAL 同步失败: {e}")

    async def _save_metadata(self) -> None:
        """保存元数据"""
        if not self._metadata:
            return
        
        async with aiofiles.open(self._meta_file, "w") as f:
            await f.write(json.dumps(self._metadata.to_dict(), indent=2))
        
        # fsync 元数据文件
        fd = os.open(str(self._meta_file), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class WALReader:
    """WAL 读取器"""

    def __init__(self, wal_path: Path):
        self._wal_path = wal_path

    async def read_all(self) -> list[WALEntry]:
        """读取所有条目"""
        entries = []
        async for entry in self.iter_entries():
            entries.append(entry)
        return entries

    async def iter_entries(self) -> AsyncIterator[WALEntry]:
        """迭代读取条目"""
        if not self._wal_path.exists():
            return
        
        async with aiofiles.open(self._wal_path, "r") as f:
            async for line in f:
                if line.strip():
                    try:
                        yield WALEntry.from_line(line)
                    except Exception as e:
                        logger.warning(f"解析 WAL 条目失败: {e}")

    async def get_content_by_type(self, log_type: str) -> str:
        """按类型获取日志内容"""
        lines = []
        async for entry in self.iter_entries():
            if entry.log_type == log_type:
                lines.append(entry.content)
        return "\n".join(lines)


class WALManager:
    """
    WAL 管理器
    
    管理所有 WAL 文件，支持：
    - 扫描未完成的 WAL
    - 清理过期 WAL
    - 统计信息
    """

    def __init__(self, config: WALConfig | None = None):
        self._config = config or WALConfig()
        self._wal_dir = Path(self._config.wal_dir)

    async def scan_pending(self) -> list[WALMetadata]:
        """扫描待处理的 WAL"""
        pending = []
        
        if not self._wal_dir.exists():
            return pending
        
        for run_dir in self._wal_dir.iterdir():
            if not run_dir.is_dir():
                continue
            
            meta_file = run_dir / "meta.json"
            if not meta_file.exists():
                continue
            
            try:
                async with aiofiles.open(meta_file, "r") as f:
                    data = json.loads(await f.read())
                    metadata = WALMetadata.from_dict(data)
                
                # 只返回需要处理的 WAL
                if metadata.state in (WALState.SEALED, WALState.UPLOADING, WALState.UPLOADED):
                    pending.append(metadata)
                    
            except Exception as e:
                logger.warning(f"读取 WAL 元数据失败 {meta_file}: {e}")
        
        return pending

    async def cleanup_expired(self) -> int:
        """清理过期的 WAL"""
        cleaned = 0
        cutoff = time.time() - self._config.retention_hours * 3600
        
        if not self._wal_dir.exists():
            return cleaned
        
        for run_dir in self._wal_dir.iterdir():
            if not run_dir.is_dir():
                continue
            
            meta_file = run_dir / "meta.json"
            if not meta_file.exists():
                # 没有元数据的目录，检查修改时间
                try:
                    stat = await aiofiles.os.stat(run_dir)
                    if stat.st_mtime < cutoff:
                        await self._remove_dir(run_dir)
                        cleaned += 1
                except Exception:
                    pass
                continue
            
            try:
                async with aiofiles.open(meta_file, "r") as f:
                    data = json.loads(await f.read())
                    metadata = WALMetadata.from_dict(data)
                
                # 已完成且过期的可以删除
                if metadata.state == WALState.COMPLETED and metadata.uploaded_at:
                    if metadata.uploaded_at < cutoff:
                        await self._remove_dir(run_dir)
                        cleaned += 1
                        
            except Exception as e:
                logger.warning(f"清理 WAL 失败 {run_dir}: {e}")
        
        if cleaned > 0:
            logger.info(f"清理了 {cleaned} 个过期 WAL")
        
        return cleaned

    async def _remove_dir(self, path: Path) -> None:
        """递归删除目录"""
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    def get_stats(self) -> dict:
        """获取统计信息"""
        stats = {
            "wal_dir": str(self._wal_dir),
            "total_wals": 0,
            "pending_wals": 0,
            "total_bytes": 0,
        }
        
        if not self._wal_dir.exists():
            return stats
        
        for run_dir in self._wal_dir.iterdir():
            if not run_dir.is_dir():
                continue
            
            stats["total_wals"] += 1
            
            wal_file = run_dir / "log.wal"
            if wal_file.exists():
                stats["total_bytes"] += wal_file.stat().st_size
            
            meta_file = run_dir / "meta.json"
            if meta_file.exists():
                try:
                    with open(meta_file) as f:
                        data = json.load(f)
                        if data.get("state") in ("sealed", "uploading"):
                            stats["pending_wals"] += 1
                except Exception:
                    pass
        
        return stats
