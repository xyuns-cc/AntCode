"""
S3/MinIO 日志存储后端

将日志存储到 S3 兼容的对象存储中。
使用公共的 S3ClientManager 管理连接。

存储结构：
    logs/{run_id}/stdout.log.gz
    logs/{run_id}/stderr.log.gz
    logs/{run_id}/system.log.gz
    logs/{run_id}/chunks/{log_type}/{offset}.chunk  # 临时分片
"""

import gzip
import hashlib
import io
import json
import os
from datetime import datetime
from typing import Any, AsyncIterator

from loguru import logger

from antcode_core.infrastructure.storage.log_storage.base import (
    LogChunk,
    LogEntry,
    LogQueryResult,
    LogStorageBackend,
    WriteResult,
)
from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager


class S3LogStorage(LogStorageBackend):
    """S3/MinIO 日志存储后端
    
    使用公共的 S3ClientManager 管理连接，避免重复创建。
    
    特点：
    - 支持预签名 URL（Worker 直传）
    - 支持分片上传（大文件）
    - 自动 gzip 压缩
    - 支持流式读取
    """

    CHUNK_SIZE = 8 * 1024 * 1024  # 8MB

    def __init__(
        self,
        bucket: str | None = None,
        prefix: str = "logs",
    ):
        """初始化 S3 日志存储
        
        Args:
            bucket: S3 桶名（默认从环境变量读取）
            prefix: 存储前缀
        """
        self.bucket = bucket or os.getenv("S3_BUCKET") or os.getenv("MINIO_BUCKET", "antcode-logs")
        self.prefix = prefix
        
        # 使用公共客户端管理器
        self._client_manager = get_s3_client_manager()
        self._bucket_ensured = False

    async def _get_client(self):
        """获取 S3 客户端（通过公共管理器）"""
        client = await self._client_manager.get_client()
        
        # 确保 bucket 存在（只检查一次）
        if not self._bucket_ensured:
            await self._ensure_bucket()
            self._bucket_ensured = True
        
        return client

    async def _ensure_bucket(self) -> None:
        """确保 bucket 存在"""
        try:
            client = await self._client_manager.get_client()
            await client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                client = await self._client_manager.get_client()
                await client.create_bucket(Bucket=self.bucket)
                logger.info(f"创建日志存储桶: {self.bucket}")
            except Exception as e:
                if "BucketAlreadyOwnedByYou" not in str(e):
                    logger.warning(f"创建桶失败（可能已存在）: {e}")

    def _build_log_path(self, run_id: str, log_type: str, compressed: bool = True) -> str:
        """构建日志存储路径"""
        ext = ".log.gz" if compressed else ".log"
        return f"{self.prefix}/{run_id}/{log_type}{ext}"

    def _build_chunk_path(self, run_id: str, log_type: str, offset: int) -> str:
        """构建分片存储路径"""
        return f"{self.prefix}/{run_id}/chunks/{log_type}/{offset:012d}.chunk"

    async def write_log(self, entry: LogEntry) -> WriteResult:
        """写入单条日志（追加模式）"""
        try:
            client = await self._get_client()
            
            # 构建日志行
            log_line = json.dumps({
                "seq": entry.sequence,
                "ts": entry.timestamp.isoformat() if entry.timestamp else datetime.now().isoformat(),
                "level": entry.level,
                "content": entry.content,
                "source": entry.source,
                "metadata": entry.metadata,
            }, ensure_ascii=False) + "\n"
            
            # 追加到日志文件（使用 JSONL 格式）
            path = f"{self.prefix}/{entry.run_id}/{entry.log_type}.jsonl"
            
            # 尝试读取现有内容
            existing_content = b""
            try:
                response = await client.get_object(Bucket=self.bucket, Key=path)
                async with response["Body"] as stream:
                    existing_content = await stream.read()
            except Exception:
                pass  # 文件不存在
            
            # 追加新内容
            new_content = existing_content + log_line.encode("utf-8")
            await client.put_object(
                Bucket=self.bucket,
                Key=path,
                Body=new_content,
                ContentType="application/x-ndjson",
            )
            
            return WriteResult(success=True, ack_offset=entry.sequence)
            
        except Exception as e:
            logger.error(f"写入日志失败: {e}")
            return WriteResult(success=False, error=str(e))

    async def write_logs_batch(self, entries: list[LogEntry]) -> WriteResult:
        """批量写入日志"""
        if not entries:
            return WriteResult(success=True)
        
        try:
            client = await self._get_client()
            
            # 按 run_id 和 log_type 分组
            groups: dict[tuple[str, str], list[LogEntry]] = {}
            for entry in entries:
                key = (entry.run_id, entry.log_type)
                if key not in groups:
                    groups[key] = []
                groups[key].append(entry)
            
            max_seq = 0
            for (run_id, log_type), group_entries in groups.items():
                # 构建日志内容
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
                
                content = "\n".join(lines) + "\n"
                path = f"{self.prefix}/{run_id}/{log_type}.jsonl"
                
                # 追加到现有文件
                existing_content = b""
                try:
                    response = await client.get_object(Bucket=self.bucket, Key=path)
                    async with response["Body"] as stream:
                        existing_content = await stream.read()
                except Exception:
                    pass
                
                new_content = existing_content + content.encode("utf-8")
                await client.put_object(
                    Bucket=self.bucket,
                    Key=path,
                    Body=new_content,
                    ContentType="application/x-ndjson",
                )
            
            return WriteResult(success=True, ack_offset=max_seq)
            
        except Exception as e:
            logger.error(f"批量写入日志失败: {e}")
            return WriteResult(success=False, error=str(e))

    async def write_chunk(self, chunk: LogChunk) -> WriteResult:
        """写入日志分片"""
        try:
            client = await self._get_client()
            
            path = self._build_chunk_path(chunk.run_id, chunk.log_type, chunk.offset)
            
            await client.put_object(
                Bucket=self.bucket,
                Key=path,
                Body=chunk.data,
                ContentType="application/octet-stream",
                Metadata={
                    "is_final": str(chunk.is_final).lower(),
                    "checksum": chunk.checksum,
                    "total_size": str(chunk.total_size),
                },
            )
            
            ack_offset = chunk.offset + len(chunk.data)
            
            return WriteResult(
                success=True,
                ack_offset=ack_offset,
                storage_path=path,
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
        """完成分片上传，合并并压缩"""
        try:
            client = await self._get_client()
            
            # 列出所有分片
            chunk_prefix = f"{self.prefix}/{run_id}/chunks/{log_type}/"
            chunks = []
            
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=chunk_prefix):
                for obj in page.get("Contents", []):
                    chunks.append(obj["Key"])
            
            if not chunks:
                return WriteResult(success=False, error="没有找到分片")
            
            # 按 offset 排序
            chunks.sort()
            
            # 合并分片并压缩
            combined = io.BytesIO()
            hasher = hashlib.sha256()
            actual_size = 0
            
            for chunk_key in chunks:
                response = await client.get_object(Bucket=self.bucket, Key=chunk_key)
                async with response["Body"] as stream:
                    data = await stream.read()
                    combined.write(data)
                    hasher.update(data)
                    actual_size += len(data)
            
            # 验证大小和校验和
            if total_size > 0 and actual_size != total_size:
                return WriteResult(
                    success=False,
                    error=f"大小不匹配: 期望 {total_size}, 实际 {actual_size}",
                )
            
            actual_checksum = hasher.hexdigest()
            if checksum and actual_checksum != checksum:
                return WriteResult(
                    success=False,
                    error=f"校验和不匹配: 期望 {checksum}, 实际 {actual_checksum}",
                )
            
            # 压缩
            combined.seek(0)
            compressed = io.BytesIO()
            with gzip.GzipFile(fileobj=compressed, mode="wb") as gz:
                gz.write(combined.read())
            
            # 上传压缩后的文件
            final_path = self._build_log_path(run_id, log_type, compressed=True)
            compressed.seek(0)
            
            await client.put_object(
                Bucket=self.bucket,
                Key=final_path,
                Body=compressed.read(),
                ContentType="application/gzip",
                Metadata={
                    "original_size": str(actual_size),
                    "checksum": actual_checksum,
                },
            )
            
            # 删除分片
            for chunk_key in chunks:
                await client.delete_object(Bucket=self.bucket, Key=chunk_key)
            
            logger.info(f"日志归档完成: {final_path}, 大小: {actual_size} -> {compressed.tell()}")
            
            return WriteResult(
                success=True,
                ack_offset=actual_size,
                storage_path=final_path,
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
            client = await self._get_client()
            
            # 确定要查询的日志类型
            log_types = [log_type] if log_type else ["stdout", "stderr", "system"]
            
            entries = []
            for lt in log_types:
                path = f"{self.prefix}/{run_id}/{lt}.jsonl"
                
                try:
                    response = await client.get_object(Bucket=self.bucket, Key=path)
                    async with response["Body"] as stream:
                        content = await stream.read()
                    
                    for line in content.decode("utf-8").strip().split("\n"):
                        if not line:
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
                            
                except Exception:
                    pass  # 文件不存在
            
            # 排序并分页
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
        try:
            client = await self._get_client()
            
            # 优先尝试压缩文件
            path = self._build_log_path(run_id, log_type, compressed=True)
            
            try:
                response = await client.get_object(Bucket=self.bucket, Key=path)
            except Exception:
                # 尝试未压缩的 JSONL 文件
                path = f"{self.prefix}/{run_id}/{log_type}.jsonl"
                response = await client.get_object(Bucket=self.bucket, Key=path)
            
            async with response["Body"] as stream:
                while chunk := await stream.read(self.CHUNK_SIZE):
                    yield chunk
                    
        except Exception as e:
            logger.error(f"获取日志流失败: {e}")
            raise

    async def delete_logs(self, run_id: str) -> bool:
        """删除日志"""
        try:
            client = await self._get_client()
            
            # 列出所有相关对象
            prefix = f"{self.prefix}/{run_id}/"
            objects_to_delete = []
            
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    objects_to_delete.append({"Key": obj["Key"]})
            
            if objects_to_delete:
                # 批量删除
                await client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": objects_to_delete},
                )
                logger.info(f"已删除日志: {run_id}, 共 {len(objects_to_delete)} 个对象")
            
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
        """获取预签名上传 URL"""
        try:
            client = await self._get_client()
            
            # 构建存储路径
            path = f"{self.prefix}/{run_id}/{filename}"
            
            url = await client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": path,
                    "ContentType": content_type,
                },
                ExpiresIn=expires_in,
            )
            
            # 构建最终访问 URL
            final_url = f"s3://{self.bucket}/{path}"
            
            return {
                "url": url,
                "path": path,
                "final_url": final_url,
                "headers": {"Content-Type": content_type},
            }
            
        except Exception as e:
            logger.error(f"生成预签名上传 URL 失败: {e}")
            return None

    async def get_presigned_download_url(
        self,
        run_id: str,
        log_type: str,
        expires_in: int = 3600,
    ) -> str | None:
        """获取预签名下载 URL"""
        try:
            client = await self._get_client()
            
            # 优先尝试压缩文件
            path = self._build_log_path(run_id, log_type, compressed=True)
            
            # 检查文件是否存在
            try:
                await client.head_object(Bucket=self.bucket, Key=path)
            except Exception:
                # 尝试未压缩文件
                path = f"{self.prefix}/{run_id}/{log_type}.jsonl"
                await client.head_object(Bucket=self.bucket, Key=path)
            
            url = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": path},
                ExpiresIn=expires_in,
            )
            
            return url
            
        except Exception as e:
            logger.error(f"生成预签名下载 URL 失败: {e}")
            return None

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            client = await self._get_client()
            await client.head_bucket(Bucket=self.bucket)
            return True
        except Exception as e:
            logger.error(f"S3 日志存储健康检查失败: {e}")
            return False
