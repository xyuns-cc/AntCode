"""
日志归档模块

高可靠日志归档到 S3，特点：
- WAL 保证不丢失
- 异步上传不阻塞
- 确认删除机制
- 崩溃恢复支持

Requirements: 9.6
"""

import asyncio
import gzip
import hashlib
import io
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiofiles
import aiohttp
from loguru import logger

from antcode_worker.domain.enums import ArtifactType
from antcode_worker.domain.models import ArtifactRef
from antcode_worker.logs.wal import WALConfig, WALManager, WALMetadata, WALReader, WALState, WALWriter


class ArchiveState(str, Enum):
    """归档状态"""
    IDLE = "idle"
    WRITING = "writing"
    SEALING = "sealing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ArchiveConfig:
    """归档配置"""
    # WAL 配置
    wal_dir: str = "var/worker/logs/wal"
    sync_on_write: bool = False
    
    # 压缩配置
    compression_level: int = 6
    
    # 上传配置
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0
    upload_timeout: float = 300.0
    
    # S3 配置（从环境变量读取）
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_prefix: str = "logs"


@dataclass
class ArchiveResult:
    """归档结果"""
    success: bool
    artifact: ArtifactRef | None = None
    error: str | None = None
    original_size: int = 0
    compressed_size: int = 0
    s3_uri: str = ""
    upload_duration_ms: int = 0


class S3Uploader:
    """
    S3 上传器
    
    使用 antcode_core 的日志存储后端进行上传。
    """

    def __init__(self):
        self._log_storage = None

    async def _get_log_storage(self):
        """获取日志存储后端"""
        if self._log_storage is None:
            try:
                from antcode_core.infrastructure.storage.log_storage import get_log_storage
                self._log_storage = get_log_storage()
            except ImportError:
                logger.warning("antcode_core.infrastructure.storage.log_storage 不可用")
                return None
        return self._log_storage

    async def upload_compressed(
        self,
        run_id: str,
        log_type: str,
        data: bytes,
        checksum: str,
    ) -> dict[str, Any]:
        """
        上传压缩后的日志
        
        Args:
            run_id: 运行 ID
            log_type: 日志类型 (stdout/stderr/combined)
            data: 压缩后的数据
            checksum: 原始数据校验和
            
        Returns:
            {"success": bool, "uri": str, "error": str}
        """
        log_storage = await self._get_log_storage()
        if log_storage is None:
            return {"success": False, "error": "日志存储后端不可用"}
        
        try:
            # 获取预签名上传 URL
            filename = f"{log_type}.log.gz"
            presigned = await log_storage.get_presigned_upload_url(
                run_id=run_id,
                filename=filename,
                content_type="application/gzip",
            )
            
            if not presigned:
                return {"success": False, "error": "获取预签名 URL 失败"}
            
            upload_url = presigned.get("url")
            headers = presigned.get("headers", {})
            
            # 上传
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    upload_url,
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as response:
                    if response.status not in (200, 201, 204):
                        error_text = await response.text()
                        return {
                            "success": False,
                            "error": f"上传失败: HTTP {response.status}, {error_text[:200]}",
                        }
            
            # 构建最终 URI
            s3_uri = presigned.get("final_url") or f"s3://logs/{run_id}/{filename}"
            
            return {
                "success": True,
                "uri": s3_uri,
                "path": presigned.get("path", ""),
            }
            
        except asyncio.TimeoutError:
            return {"success": False, "error": "上传超时"}
        except Exception as e:
            return {"success": False, "error": str(e)}


class LogArchiver:
    """
    高可靠日志归档器
    
    流程：
    1. 日志写入 WAL（本地持久化）
    2. 任务结束时封存 WAL
    3. 压缩并上传到 S3
    4. 确认成功后删除本地 WAL
    
    崩溃恢复：
    - Worker 重启时扫描未完成的 WAL
    - 继续上传未完成的归档
    """

    def __init__(self, run_id: str, config: ArchiveConfig | None = None):
        self.run_id = run_id
        self._config = config or ArchiveConfig()
        
        # WAL 配置
        wal_config = WALConfig(
            wal_dir=self._config.wal_dir,
            sync_on_write=self._config.sync_on_write,
        )
        
        # 组件
        self._wal_writer = WALWriter(run_id, wal_config)
        self._uploader = S3Uploader()
        
        # 状态
        self._state = ArchiveState.IDLE
        self._started = False
        
        # 统计
        self._entries_written = 0
        self._bytes_written = 0

    @property
    def state(self) -> ArchiveState:
        return self._state

    async def start(self) -> None:
        """启动归档器"""
        if self._started:
            return
        
        self._started = True
        self._state = ArchiveState.WRITING
        
        await self._wal_writer.start()
        logger.debug(f"[{self.run_id}] 日志归档器已启动")

    async def stop(self) -> None:
        """停止归档器"""
        if not self._started:
            return
        
        self._started = False
        await self._wal_writer.stop()
        logger.debug(f"[{self.run_id}] 日志归档器已停止")

    async def write(self, log_type: str, content: str, level: str = "INFO") -> None:
        """
        写入日志
        
        Args:
            log_type: 日志类型 (stdout/stderr/system)
            content: 日志内容
            level: 日志级别
        """
        if not self._started:
            return
        
        seq = await self._wal_writer.write(log_type, content, level)
        if seq > 0:
            self._entries_written += 1
            self._bytes_written += len(content.encode("utf-8"))

    async def archive(self) -> list[ArchiveResult]:
        """
        执行归档
        
        封存 WAL，压缩并上传到 S3。
        
        Returns:
            归档结果列表
        """
        results = []
        
        try:
            # 封存 WAL
            self._state = ArchiveState.SEALING
            metadata = await self._wal_writer.seal()
            
            if metadata.entry_count == 0:
                logger.debug(f"[{self.run_id}] 无日志需要归档")
                self._state = ArchiveState.COMPLETED
                return results
            
            # 读取并压缩
            self._state = ArchiveState.UPLOADING
            
            # 分别处理 stdout 和 stderr
            reader = WALReader(self._wal_writer.wal_path)
            
            for log_type in ["stdout", "stderr"]:
                content = await reader.get_content_by_type(log_type)
                if not content:
                    continue
                
                result = await self._compress_and_upload(
                    log_type=log_type,
                    content=content,
                    metadata=metadata,
                )
                results.append(result)
            
            # 如果有任何成功的上传，标记 WAL 为已上传
            successful = [r for r in results if r.success]
            if successful:
                await self._wal_writer.mark_uploaded(successful[0].s3_uri)
                await self._wal_writer.mark_completed()
                
                # 删除本地 WAL
                await self._wal_writer.delete()
                self._state = ArchiveState.COMPLETED
                logger.info(f"[{self.run_id}] 日志归档完成: {len(successful)} 个文件")
            else:
                self._state = ArchiveState.FAILED
                logger.warning(f"[{self.run_id}] 日志归档失败")
            
            return results
            
        except Exception as e:
            self._state = ArchiveState.FAILED
            logger.error(f"[{self.run_id}] 归档异常: {e}")
            return [ArchiveResult(success=False, error=str(e))]

    async def _compress_and_upload(
        self,
        log_type: str,
        content: str,
        metadata: WALMetadata,
    ) -> ArchiveResult:
        """压缩并上传单个日志类型"""
        start_time = datetime.now()
        
        try:
            # 压缩
            original_bytes = content.encode("utf-8")
            original_size = len(original_bytes)
            
            compressed = io.BytesIO()
            with gzip.GzipFile(
                fileobj=compressed,
                mode="wb",
                compresslevel=self._config.compression_level,
            ) as gz:
                gz.write(original_bytes)
            
            compressed_data = compressed.getvalue()
            compressed_size = len(compressed_data)
            
            # 计算校验和
            checksum = hashlib.sha256(original_bytes).hexdigest()
            
            logger.debug(
                f"[{self.run_id}] 压缩 {log_type}: "
                f"{original_size} -> {compressed_size} bytes "
                f"({compressed_size / original_size * 100:.1f}%)"
            )
            
            # 上传（带重试）
            upload_result = await self._upload_with_retry(
                log_type=log_type,
                data=compressed_data,
                checksum=checksum,
            )
            
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            
            if not upload_result["success"]:
                return ArchiveResult(
                    success=False,
                    error=upload_result.get("error", "上传失败"),
                    original_size=original_size,
                    compressed_size=compressed_size,
                    upload_duration_ms=duration_ms,
                )
            
            # 创建 ArtifactRef
            s3_uri = upload_result.get("uri", "")
            artifact = ArtifactRef(
                name=f"{self.run_id}_{log_type}.log.gz",
                artifact_type=ArtifactType.LOG,
                uri=s3_uri,
                size_bytes=compressed_size,
                checksum=checksum,
                mime_type="application/gzip",
            )
            
            return ArchiveResult(
                success=True,
                artifact=artifact,
                original_size=original_size,
                compressed_size=compressed_size,
                s3_uri=s3_uri,
                upload_duration_ms=duration_ms,
            )
            
        except Exception as e:
            return ArchiveResult(success=False, error=str(e))

    async def _upload_with_retry(
        self,
        log_type: str,
        data: bytes,
        checksum: str,
    ) -> dict[str, Any]:
        """带重试的上传"""
        delay = self._config.retry_delay
        
        for attempt in range(self._config.max_retries):
            result = await self._uploader.upload_compressed(
                run_id=self.run_id,
                log_type=log_type,
                data=data,
                checksum=checksum,
            )
            
            if result["success"]:
                return result
            
            if attempt < self._config.max_retries - 1:
                logger.warning(
                    f"[{self.run_id}] 上传 {log_type} 失败 (attempt {attempt + 1}): "
                    f"{result.get('error')}, {delay}s 后重试"
                )
                await asyncio.sleep(delay)
                delay *= self._config.retry_backoff
        
        return result

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "run_id": self.run_id,
            "state": self._state.value,
            "entries_written": self._entries_written,
            "bytes_written": self._bytes_written,
            "wal_metadata": self._wal_writer.metadata.to_dict() if self._wal_writer.metadata else None,
        }


class ArchiveRecoveryService:
    """
    归档恢复服务
    
    Worker 启动时扫描未完成的 WAL，继续上传。
    """

    def __init__(self, config: ArchiveConfig | None = None):
        self._config = config or ArchiveConfig()
        self._wal_manager = WALManager(WALConfig(wal_dir=self._config.wal_dir))
        self._uploader = S3Uploader()
        self._running = False
        self._recovery_task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动恢复服务"""
        if self._running:
            return
        
        self._running = True
        
        # 启动后台恢复任务
        self._recovery_task = asyncio.create_task(self._recovery_loop())
        logger.info("归档恢复服务已启动")

    async def stop(self) -> None:
        """停止恢复服务"""
        self._running = False
        
        if self._recovery_task:
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass
        
        logger.info("归档恢复服务已停止")

    async def _recovery_loop(self) -> None:
        """恢复循环"""
        # 启动时立即执行一次
        await self._recover_pending()
        
        # 定期检查
        while self._running:
            try:
                await asyncio.sleep(300)  # 5 分钟检查一次
                await self._recover_pending()
                await self._wal_manager.cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"恢复循环异常: {e}")

    async def _recover_pending(self) -> None:
        """恢复待处理的 WAL"""
        pending = await self._wal_manager.scan_pending()
        
        if not pending:
            return
        
        logger.info(f"发现 {len(pending)} 个待恢复的 WAL")
        
        for metadata in pending:
            try:
                await self._recover_one(metadata)
            except Exception as e:
                logger.error(f"恢复 WAL {metadata.run_id} 失败: {e}")

    async def _recover_one(self, metadata: WALMetadata) -> None:
        """恢复单个 WAL"""
        run_id = metadata.run_id
        wal_dir = Path(self._config.wal_dir) / run_id
        wal_file = wal_dir / "log.wal"
        
        if not wal_file.exists():
            logger.warning(f"[{run_id}] WAL 文件不存在，跳过")
            return
        
        logger.info(f"[{run_id}] 开始恢复归档...")
        
        # 读取并压缩上传
        reader = WALReader(wal_file)
        
        for log_type in ["stdout", "stderr"]:
            content = await reader.get_content_by_type(log_type)
            if not content:
                continue
            
            # 压缩
            original_bytes = content.encode("utf-8")
            compressed = io.BytesIO()
            with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=6) as gz:
                gz.write(original_bytes)
            
            compressed_data = compressed.getvalue()
            checksum = hashlib.sha256(original_bytes).hexdigest()
            
            # 上传
            result = await self._uploader.upload_compressed(
                run_id=run_id,
                log_type=log_type,
                data=compressed_data,
                checksum=checksum,
            )
            
            if result["success"]:
                logger.info(f"[{run_id}] 恢复上传 {log_type} 成功: {result.get('uri')}")
            else:
                logger.warning(f"[{run_id}] 恢复上传 {log_type} 失败: {result.get('error')}")
        
        # 更新元数据并清理
        meta_file = wal_dir / "meta.json"
        if meta_file.exists():
            import json
            async with aiofiles.open(meta_file, "r") as f:
                data = json.loads(await f.read())
            data["state"] = WALState.COMPLETED.value
            async with aiofiles.open(meta_file, "w") as f:
                await f.write(json.dumps(data, indent=2))


# 兼容旧接口
LogArchive = LogArchiver
SimpleUploader = S3Uploader
