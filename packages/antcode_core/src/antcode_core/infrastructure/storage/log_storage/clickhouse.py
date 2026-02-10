"""
ClickHouse 日志存储后端

用于大规模日志存储和分析。

特点：
- 高性能批量写入
- 时间序列查询优化
- 自动分区和 TTL
- 支持全文搜索

依赖：
    pip install clickhouse-connect

表结构：
    CREATE TABLE antcode.logs (
        run_id String,
        log_type Enum8('stdout' = 1, 'stderr' = 2, 'system' = 3),
        sequence UInt64,
        timestamp DateTime64(3),
        level String,
        content String,
        source Nullable(String),
        metadata String,
        INDEX idx_content content TYPE tokenbf_v1(10240, 3, 0) GRANULARITY 4
    ) ENGINE = MergeTree()
    PARTITION BY toYYYYMM(timestamp)
    ORDER BY (run_id, log_type, sequence)
    TTL timestamp + INTERVAL 30 DAY;
"""

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


# 建表 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {database}.logs (
    run_id String,
    log_type Enum8('stdout' = 1, 'stderr' = 2, 'system' = 3),
    sequence UInt64,
    timestamp DateTime64(3),
    level String DEFAULT 'INFO',
    content String,
    source Nullable(String),
    metadata String DEFAULT '{}',
    INDEX idx_run_id run_id TYPE bloom_filter GRANULARITY 1,
    INDEX idx_content content TYPE tokenbf_v1(10240, 3, 0) GRANULARITY 4
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (run_id, log_type, sequence)
TTL timestamp + INTERVAL {retention_days} DAY
SETTINGS index_granularity = 8192;
"""

# 日志分片临时表（用于大文件上传）
CREATE_CHUNKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {database}.log_chunks (
    run_id String,
    log_type String,
    offset UInt64,
    data String,
    is_final UInt8 DEFAULT 0,
    checksum String DEFAULT '',
    total_size Int64 DEFAULT -1,
    created_at DateTime64(3) DEFAULT now64(3)
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(created_at)
ORDER BY (run_id, log_type, offset)
TTL created_at + INTERVAL 1 DAY;
"""


class ClickHouseLogStorage(LogStorageBackend):
    """ClickHouse 日志存储后端
    
    特点：
    - 高性能批量写入（使用 INSERT ... VALUES 批量插入）
    - 时间序列查询优化
    - 支持全文搜索（tokenbf_v1 索引）
    - 自动分区和 TTL
    
    配置环境变量：
        CLICKHOUSE_HOST: 主机地址（默认 localhost）
        CLICKHOUSE_PORT: HTTP 端口（默认 8123）
        CLICKHOUSE_DATABASE: 数据库名（默认 antcode）
        CLICKHOUSE_USER: 用户名（默认 default）
        CLICKHOUSE_PASSWORD: 密码
        CLICKHOUSE_RETENTION_DAYS: 日志保留天数（默认 30）
    """

    # 批量写入阈值
    BATCH_SIZE = 1000
    
    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
        user: str | None = None,
        password: str | None = None,
        retention_days: int | None = None,
    ):
        """初始化 ClickHouse 日志存储
        
        Args:
            host: ClickHouse 主机
            port: ClickHouse HTTP 端口
            database: 数据库名
            user: 用户名
            password: 密码
            retention_days: 日志保留天数
        """
        self.host = host or os.getenv("CLICKHOUSE_HOST", "localhost")
        self.port = port or int(os.getenv("CLICKHOUSE_PORT", "8123"))
        self.database = database or os.getenv("CLICKHOUSE_DATABASE", "antcode")
        self.user = user or os.getenv("CLICKHOUSE_USER", "default")
        self.password = password or os.getenv("CLICKHOUSE_PASSWORD", "")
        self.retention_days = retention_days or int(os.getenv("CLICKHOUSE_RETENTION_DAYS", "30"))
        
        self._client = None
        self._initialized = False
        
        # 写入缓冲
        self._buffer: list[LogEntry] = []

    async def _get_client(self):
        """获取 ClickHouse 客户端"""
        if self._client is None:
            try:
                import clickhouse_connect
            except ImportError:
                raise ImportError("请安装 clickhouse-connect: pip install clickhouse-connect")
            
            self._client = clickhouse_connect.get_client(
                host=self.host,
                port=self.port,
                database=self.database,
                username=self.user,
                password=self.password,
            )
            
            # 初始化表结构
            if not self._initialized:
                await self._init_tables()
                self._initialized = True
        
        return self._client

    async def _init_tables(self) -> None:
        """初始化表结构"""
        try:
            # 创建数据库
            self._client.command(f"CREATE DATABASE IF NOT EXISTS {self.database}")
            
            # 创建日志表
            create_logs_sql = CREATE_TABLE_SQL.format(
                database=self.database,
                retention_days=self.retention_days,
            )
            self._client.command(create_logs_sql)
            
            # 创建分片临时表
            create_chunks_sql = CREATE_CHUNKS_TABLE_SQL.format(database=self.database)
            self._client.command(create_chunks_sql)
            
            logger.info(f"ClickHouse 日志表已初始化: {self.database}.logs")
            
        except Exception as e:
            logger.error(f"初始化 ClickHouse 表失败: {e}")
            raise

    def _log_type_to_enum(self, log_type: str) -> str:
        """转换日志类型为枚举值"""
        mapping = {"stdout": "stdout", "stderr": "stderr", "system": "system"}
        return mapping.get(log_type, "stdout")

    async def write_log(self, entry: LogEntry) -> WriteResult:
        """写入单条日志"""
        try:
            client = await self._get_client()
            
            # 准备数据
            data = [[
                entry.run_id,
                self._log_type_to_enum(entry.log_type),
                entry.sequence,
                entry.timestamp or datetime.now(),
                entry.level,
                entry.content,
                entry.source,
                json.dumps(entry.metadata, ensure_ascii=False) if entry.metadata else "{}",
            ]]
            
            # 插入数据
            client.insert(
                f"{self.database}.logs",
                data,
                column_names=[
                    "run_id", "log_type", "sequence", "timestamp",
                    "level", "content", "source", "metadata"
                ],
            )
            
            return WriteResult(success=True, ack_offset=entry.sequence)
            
        except Exception as e:
            logger.error(f"写入日志到 ClickHouse 失败: {e}")
            return WriteResult(success=False, error=str(e))

    async def write_logs_batch(self, entries: list[LogEntry]) -> WriteResult:
        """批量写入日志"""
        if not entries:
            return WriteResult(success=True)
        
        try:
            client = await self._get_client()
            
            # 准备批量数据
            data = []
            max_seq = 0
            
            for entry in entries:
                data.append([
                    entry.run_id,
                    self._log_type_to_enum(entry.log_type),
                    entry.sequence,
                    entry.timestamp or datetime.now(),
                    entry.level,
                    entry.content,
                    entry.source,
                    json.dumps(entry.metadata, ensure_ascii=False) if entry.metadata else "{}",
                ])
                max_seq = max(max_seq, entry.sequence)
            
            # 批量插入
            client.insert(
                f"{self.database}.logs",
                data,
                column_names=[
                    "run_id", "log_type", "sequence", "timestamp",
                    "level", "content", "source", "metadata"
                ],
            )
            
            logger.debug(f"批量写入 {len(entries)} 条日志到 ClickHouse")
            return WriteResult(success=True, ack_offset=max_seq)
            
        except Exception as e:
            logger.error(f"批量写入日志到 ClickHouse 失败: {e}")
            return WriteResult(success=False, error=str(e))

    async def write_chunk(self, chunk: LogChunk) -> WriteResult:
        """写入日志分片"""
        try:
            client = await self._get_client()
            
            import base64
            
            # 将二进制数据编码为 base64
            data_b64 = base64.b64encode(chunk.data).decode("utf-8")
            
            # 插入分片
            client.insert(
                f"{self.database}.log_chunks",
                [[
                    chunk.run_id,
                    chunk.log_type,
                    chunk.offset,
                    data_b64,
                    1 if chunk.is_final else 0,
                    chunk.checksum,
                    chunk.total_size,
                    datetime.now(),
                ]],
                column_names=[
                    "run_id", "log_type", "offset", "data",
                    "is_final", "checksum", "total_size", "created_at"
                ],
            )
            
            ack_offset = chunk.offset + len(chunk.data)
            return WriteResult(success=True, ack_offset=ack_offset)
            
        except Exception as e:
            logger.error(f"写入日志分片到 ClickHouse 失败: {e}")
            return WriteResult(success=False, ack_offset=chunk.offset, error=str(e))

    async def finalize_chunks(
        self,
        run_id: str,
        log_type: str,
        total_size: int,
        checksum: str,
    ) -> WriteResult:
        """完成分片上传，合并到日志表"""
        try:
            client = await self._get_client()
            
            import base64
            import hashlib
            
            # 查询所有分片
            result = client.query(
                f"""
                SELECT offset, data FROM {self.database}.log_chunks
                WHERE run_id = %(run_id)s AND log_type = %(log_type)s
                ORDER BY offset
                """,
                parameters={"run_id": run_id, "log_type": log_type},
            )
            
            if not result.result_rows:
                return WriteResult(success=False, error="没有找到分片")
            
            # 合并分片
            combined = bytearray()
            hasher = hashlib.sha256()
            
            for row in result.result_rows:
                offset, data_b64 = row
                data = base64.b64decode(data_b64)
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
                return WriteResult(success=False, error="校验和不匹配")
            
            # 解析日志内容并插入到日志表
            content = combined.decode("utf-8", errors="replace")
            lines = content.strip().split("\n")
            
            entries = []
            for seq, line in enumerate(lines):
                if line.strip():
                    entries.append(LogEntry(
                        run_id=run_id,
                        log_type=log_type,
                        content=line,
                        sequence=seq,
                        timestamp=datetime.now(),
                    ))
            
            if entries:
                await self.write_logs_batch(entries)
            
            # 删除分片
            client.command(
                f"""
                ALTER TABLE {self.database}.log_chunks
                DELETE WHERE run_id = %(run_id)s AND log_type = %(log_type)s
                """,
                parameters={"run_id": run_id, "log_type": log_type},
            )
            
            logger.info(f"日志归档完成: run_id={run_id}, log_type={log_type}, lines={len(entries)}")
            
            return WriteResult(
                success=True,
                ack_offset=actual_size,
                storage_path=f"clickhouse://{self.database}.logs/{run_id}/{log_type}",
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
            
            # 构建查询条件
            conditions = ["run_id = %(run_id)s", "sequence >= %(start_seq)s"]
            params = {"run_id": run_id, "start_seq": start_seq, "limit": limit + 1}
            
            if log_type:
                conditions.append("log_type = %(log_type)s")
                params["log_type"] = log_type
            
            # 查询
            query = f"""
                SELECT run_id, log_type, sequence, timestamp, level, content, source, metadata
                FROM {self.database}.logs
                WHERE {' AND '.join(conditions)}
                ORDER BY sequence
                LIMIT %(limit)s
            """
            
            result = client.query(query, parameters=params)
            
            entries = []
            for row in result.result_rows[:limit]:
                run_id, lt, seq, ts, level, content, source, metadata = row
                entries.append(LogEntry(
                    run_id=run_id,
                    log_type=lt,
                    sequence=seq,
                    timestamp=ts,
                    level=level,
                    content=content,
                    source=source,
                    metadata=json.loads(metadata) if metadata else {},
                ))
            
            has_more = len(result.result_rows) > limit
            next_cursor = str(entries[-1].sequence + 1) if entries and has_more else None
            
            # 获取总数
            count_query = f"""
                SELECT count() FROM {self.database}.logs
                WHERE run_id = %(run_id)s
            """
            count_result = client.query(count_query, parameters={"run_id": run_id})
            total = count_result.result_rows[0][0] if count_result.result_rows else 0
            
            return LogQueryResult(
                entries=entries,
                total=total,
                has_more=has_more,
                next_cursor=next_cursor,
            )
            
        except Exception as e:
            logger.error(f"查询日志失败: {e}")
            return LogQueryResult(entries=[], total=0, has_more=False)

    async def get_log_stream(
        self,
        run_id: str,
        log_type: str,
    ) -> AsyncIterator[bytes]:
        """获取日志流（导出为 JSONL 格式）"""
        try:
            client = await self._get_client()
            
            # 分批查询并流式返回
            offset = 0
            batch_size = 10000
            
            while True:
                result = client.query(
                    f"""
                    SELECT sequence, timestamp, level, content, source
                    FROM {self.database}.logs
                    WHERE run_id = %(run_id)s AND log_type = %(log_type)s
                    ORDER BY sequence
                    LIMIT %(limit)s OFFSET %(offset)s
                    """,
                    parameters={
                        "run_id": run_id,
                        "log_type": log_type,
                        "limit": batch_size,
                        "offset": offset,
                    },
                )
                
                if not result.result_rows:
                    break
                
                # 转换为 JSONL 格式
                lines = []
                for row in result.result_rows:
                    seq, ts, level, content, source = row
                    lines.append(json.dumps({
                        "seq": seq,
                        "ts": ts.isoformat() if ts else None,
                        "level": level,
                        "content": content,
                        "source": source,
                    }, ensure_ascii=False))
                
                yield ("\n".join(lines) + "\n").encode("utf-8")
                
                if len(result.result_rows) < batch_size:
                    break
                
                offset += batch_size
                
        except Exception as e:
            logger.error(f"获取日志流失败: {e}")
            raise

    async def delete_logs(self, run_id: str) -> bool:
        """删除日志"""
        try:
            client = await self._get_client()
            
            # 删除日志
            client.command(
                f"ALTER TABLE {self.database}.logs DELETE WHERE run_id = %(run_id)s",
                parameters={"run_id": run_id},
            )
            
            # 删除分片
            client.command(
                f"ALTER TABLE {self.database}.log_chunks DELETE WHERE run_id = %(run_id)s",
                parameters={"run_id": run_id},
            )
            
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
        """ClickHouse 不支持预签名 URL，返回直接写入标识"""
        # 返回一个标识，表示应该直接通过 write_chunk 写入
        return {
            "url": f"clickhouse://{self.database}.log_chunks/{run_id}/{filename}",
            "path": f"{run_id}/{filename}",
            "final_url": f"clickhouse://{self.database}.logs/{run_id}",
            "headers": {},
            "direct_write": True,  # 标识需要直接写入
        }

    async def get_presigned_download_url(
        self,
        run_id: str,
        log_type: str,
        expires_in: int = 3600,
    ) -> str | None:
        """ClickHouse 不支持预签名 URL"""
        # 返回一个标识 URL，实际下载需要通过 get_log_stream
        return f"clickhouse://{self.database}.logs/{run_id}/{log_type}"

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            client = await self._get_client()
            result = client.query("SELECT 1")
            return result.result_rows[0][0] == 1
        except Exception as e:
            logger.error(f"ClickHouse 健康检查失败: {e}")
            return False

    async def search_logs(
        self,
        run_id: str,
        keyword: str,
        log_type: str | None = None,
        limit: int = 100,
    ) -> LogQueryResult:
        """全文搜索日志
        
        Args:
            run_id: 运行 ID
            keyword: 搜索关键词
            log_type: 日志类型（可选）
            limit: 返回数量限制
            
        Returns:
            查询结果
        """
        try:
            client = await self._get_client()
            
            conditions = ["run_id = %(run_id)s", "content LIKE %(keyword)s"]
            params = {"run_id": run_id, "keyword": f"%{keyword}%", "limit": limit}
            
            if log_type:
                conditions.append("log_type = %(log_type)s")
                params["log_type"] = log_type
            
            result = client.query(
                f"""
                SELECT run_id, log_type, sequence, timestamp, level, content, source, metadata
                FROM {self.database}.logs
                WHERE {' AND '.join(conditions)}
                ORDER BY sequence
                LIMIT %(limit)s
                """,
                parameters=params,
            )
            
            entries = []
            for row in result.result_rows:
                run_id, lt, seq, ts, level, content, source, metadata = row
                entries.append(LogEntry(
                    run_id=run_id,
                    log_type=lt,
                    sequence=seq,
                    timestamp=ts,
                    level=level,
                    content=content,
                    source=source,
                    metadata=json.loads(metadata) if metadata else {},
                ))
            
            return LogQueryResult(
                entries=entries,
                total=len(entries),
                has_more=len(entries) >= limit,
            )
            
        except Exception as e:
            logger.error(f"搜索日志失败: {e}")
            return LogQueryResult(entries=[], total=0, has_more=False)

    async def get_log_stats(self, run_id: str) -> dict[str, Any]:
        """获取日志统计信息
        
        Args:
            run_id: 运行 ID
            
        Returns:
            统计信息
        """
        try:
            client = await self._get_client()
            
            result = client.query(
                f"""
                SELECT
                    log_type,
                    count() as count,
                    min(timestamp) as first_ts,
                    max(timestamp) as last_ts,
                    sum(length(content)) as total_bytes
                FROM {self.database}.logs
                WHERE run_id = %(run_id)s
                GROUP BY log_type
                """,
                parameters={"run_id": run_id},
            )
            
            stats = {
                "run_id": run_id,
                "by_type": {},
                "total_count": 0,
                "total_bytes": 0,
            }
            
            for row in result.result_rows:
                log_type, count, first_ts, last_ts, total_bytes = row
                stats["by_type"][log_type] = {
                    "count": count,
                    "first_timestamp": first_ts.isoformat() if first_ts else None,
                    "last_timestamp": last_ts.isoformat() if last_ts else None,
                    "total_bytes": total_bytes,
                }
                stats["total_count"] += count
                stats["total_bytes"] += total_bytes
            
            return stats
            
        except Exception as e:
            logger.error(f"获取日志统计失败: {e}")
            return {"run_id": run_id, "error": str(e)}
