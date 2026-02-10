"""
本地文件日志存储后端

用于开发和测试环境。
"""

import gzip
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

import aiofiles
import aiofiles.os
from loguru import logger

from antcode_core.infrastructure.storage.log_storage.base import (
    LogChunk,
    LogEntry,
    LogQueryResult,
    LogStorageBackend,
    WriteResult,
)


class LocalLogStorage(LogStorageBackend):
    """本地文件日志存储后端
    
    存储结构：
        {base_dir}/logs/{run_id}/stdout.jsonl
        {base_dir}/logs/{run_id}/stderr.jsonl
        {base_dir}/logs/{run_id}/stdout.log.gz  # 归档后
    """

    def __init__(self, base_dir: str | None = None):
        """初始化本地日志存储
        
        Args:
            base_dir: 基础目录，默认为 data/backend/storage
        """
        from antcode_core.common.config import settings
        self.base_dir = Path(base_dir or os.path.join(settings.data_dir, "storage"))
        self.logs_dir = self.base_dir / "logs"

    def _get_log_dir(self, run_id: str) -> Path:
        """获取日志目录"""
        return self.logs_dir / run_id

    def _get_log_path(self, run_id: str, log_type: str, compressed: bool = False) -> Path:
        """获取日志文件路径"""
        ext = ".log.gz" if compressed else ".jsonl"
        return self._get_log_dir(run_id) / f"{log_type}{ext}"

    def _get_chunk_dir(self, run_id: str, log_type: str) -> Path:
        """获取分片目录"""
        return self._get_log_dir(run_id) / "chunks" / log_type

    async def write_log(self, entry: LogEntry) -> WriteResult:
        """写入单条日志"""
        try:
            log_dir = self._get_log_dir(entry.run_id)
            await aiofiles.os.makedirs(log_dir, exist_ok=True)
            
            log_path = self._get_log_path(entry.run_id, entry.log_type)
            
            log_line = json.dumps({
                "seq": entry.sequence,
                "ts": entry.timestamp.isoformat() if entry.timestamp else datetime.now().isoformat(),
                "level": entry.level,
                "content": entry.content,
                "source": entry.source,
            }, ensure_ascii=False) + "\n"
            
            async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
                await f.write(log_line)
            
            return WriteResult(success=True, ack_offset=entry.sequence)
            
        except Exception as e:
            logger.error(f"写入日志失败: {e}")
            return WriteResult(success=False, error=str(e))

    async def write_logs_batch(self, entries: list[LogEntry]) -> WriteResult:
        """批量写入日志"""
        if not entries:
            return WriteResult(success=True)
        
        try:
            # 按 run_id 和 log_type 分组
            groups: dict[tuple[str, str], list[LogEntry]] = {}
            for entry in entries:
                key = (entry.run_id, entry.log_type)
                if key not in groups:
                    groups[key] = []
                groups[key].append(entry)
            
            max_seq = 0
            for (run_id, log_type), group_entries in groups.items():
                log_dir = self._get_log_dir(run_id)
                await aiofiles.os.makedirs(log_dir, exist_ok=True)
                
                log_path = self._get_log_path(run_id, log_type)
                
                lines = []
                for entry in sorted(group_entries, key=lambda e: e.sequence):
                    lines.append(json.dumps({
                        "seq": entry.sequence,
                        "ts": entry.timestamp.isoformat() if entry.timestamp else datetime.now().isoformat(),
                        "level": entry.level,
                        "content": entry.content,
                        "source": entry.source,
                    }, ensure_ascii=False))
                    max_seq = max(max_seq, entry.sequence)
                
                async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
                    await f.write("\n".join(lines) + "\n")
            
            return WriteResult(success=True, ack_offset=max_seq)
            
        except Exception as e:
            logger.error(f"批量写入日志失败: {e}")
            return WriteResult(success=False, error=str(e))

    async def write_chunk(self, chunk: LogChunk) -> WriteResult:
        """写入日志分片"""
        try:
            chunk_dir = self._get_chunk_dir(chunk.run_id, chunk.log_type)
            await aiofiles.os.makedirs(chunk_dir, exist_ok=True)
            
            chunk_path = chunk_dir / f"{chunk.offset:012d}.chunk"
            
            async with aiofiles.open(chunk_path, "wb") as f:
                await f.write(chunk.data)
            
            ack_offset = chunk.offset + len(chunk.data)
            
            return WriteResult(
                success=True,
                ack_offset=ack_offset,
                storage_path=str(chunk_path),
            )
            
        except Exception as e:
            logger.error(f"写入日志分片失败: {e}")
            return WriteResult(success=False, ack_offset=chunk.offset, error=str(e))

    async def finalize_chunks(
        self,
        run_id: str,
        log_type: str,
        total_size: int,
        checksum: str,
    ) -> WriteResult:
        """完成分片上传"""
        try:
            chunk_dir = self._get_chunk_dir(run_id, log_type)
            
            if not chunk_dir.exists():
                return WriteResult(success=False, error="没有找到分片")
            
            # 获取所有分片并排序
            chunks = sorted(chunk_dir.glob("*.chunk"))
            
            if not chunks:
                return WriteResult(success=False, error="没有找到分片")
            
            # 合并分片
            combined = bytearray()
            hasher = hashlib.sha256()
            
            for chunk_path in chunks:
                async with aiofiles.open(chunk_path, "rb") as f:
                    data = await f.read()
                    combined.extend(data)
                    hasher.update(data)
            
            actual_size = len(combined)
            actual_checksum = hasher.hexdigest()
            
            # 验证
            if total_size > 0 and actual_size != total_size:
                return WriteResult(
                    success=False,
                    error=f"大小不匹配: 期望 {total_size}, 实际 {actual_size}",
                )
            
            if checksum and actual_checksum != checksum:
                return WriteResult(
                    success=False,
                    error="校验和不匹配",
                )
            
            # 压缩并保存
            final_path = self._get_log_path(run_id, log_type, compressed=True)
            
            with gzip.open(final_path, "wb") as gz:
                gz.write(combined)
            
            # 删除分片
            import shutil
            shutil.rmtree(chunk_dir)
            
            logger.info(f"日志归档完成: {final_path}")
            
            return WriteResult(
                success=True,
                ack_offset=actual_size,
                storage_path=str(final_path),
            )
            
        except Exception as e:
            logger.error(f"合并日志分片失败: {e}")
            return WriteResult(success=False, error=str(e))

    async def query_logs(
        self,
        run_id: str,
        log_type: str | None = None,
        start_seq: int = 0,
        limit: int = 100,
        cursor: str | None = None,
    ) -> LogQueryResult:
        """查询日志"""
        try:
            log_types = [log_type] if log_type else ["stdout", "stderr", "system"]
            entries = []
            
            for lt in log_types:
                log_path = self._get_log_path(run_id, lt)
                
                if not log_path.exists():
                    continue
                
                async with aiofiles.open(log_path, "r", encoding="utf-8") as f:
                    async for line in f:
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            if data.get("seq", 0) >= start_seq:
                                entries.append(LogEntry(
                                    run_id=run_id,
                                    log_type=lt,
                                    content=data.get("content", ""),
                                    sequence=data.get("seq", 0),
                                    timestamp=datetime.fromisoformat(data["ts"]) if data.get("ts") else None,
                                    level=data.get("level", "INFO"),
                                    source=data.get("source"),
                                ))
                        except json.JSONDecodeError:
                            continue
            
            entries.sort(key=lambda e: e.sequence)
            total = len(entries)
            entries = entries[:limit]
            
            return LogQueryResult(
                entries=entries,
                total=total,
                has_more=total > limit,
                next_cursor=str(entries[-1].sequence + 1) if entries and total > limit else None,
            )
            
        except Exception as e:
            logger.error(f"查询日志失败: {e}")
            return LogQueryResult(entries=[], total=0, has_more=False)

    async def get_log_stream(
        self,
        run_id: str,
        log_type: str,
    ) -> AsyncIterator[bytes]:
        """获取日志流"""
        # 优先尝试压缩文件
        compressed_path = self._get_log_path(run_id, log_type, compressed=True)
        
        if compressed_path.exists():
            async with aiofiles.open(compressed_path, "rb") as f:
                while chunk := await f.read(8 * 1024 * 1024):
                    yield chunk
        else:
            # 尝试 JSONL 文件
            log_path = self._get_log_path(run_id, log_type)
            if log_path.exists():
                async with aiofiles.open(log_path, "rb") as f:
                    while chunk := await f.read(8 * 1024 * 1024):
                        yield chunk

    async def delete_logs(self, run_id: str) -> bool:
        """删除日志"""
        try:
            log_dir = self._get_log_dir(run_id)
            
            if log_dir.exists():
                import shutil
                shutil.rmtree(log_dir)
                logger.info(f"已删除日志: {run_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"删除日志失败: {e}")
            return False

    async def get_presigned_upload_url(
        self,
        run_id: str,
        filename: str,
        content_type: str = "application/gzip",
        expires_in: int = 3600,
    ) -> dict[str, Any] | None:
        """本地存储不支持预签名 URL"""
        # 返回本地路径信息
        log_dir = self._get_log_dir(run_id)
        path = log_dir / filename
        
        return {
            "url": f"file://{path}",
            "path": str(path),
            "final_url": f"file://{path}",
            "headers": {},
        }

    async def get_presigned_download_url(
        self,
        run_id: str,
        log_type: str,
        expires_in: int = 3600,
    ) -> str | None:
        """本地存储返回文件路径"""
        compressed_path = self._get_log_path(run_id, log_type, compressed=True)
        
        if compressed_path.exists():
            return f"file://{compressed_path}"
        
        log_path = self._get_log_path(run_id, log_type)
        if log_path.exists():
            return f"file://{log_path}"
        
        return None

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            await aiofiles.os.makedirs(self.logs_dir, exist_ok=True)
            return True
        except Exception:
            return False
