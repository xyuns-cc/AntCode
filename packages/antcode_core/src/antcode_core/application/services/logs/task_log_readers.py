"""Bounded PostgreSQL and Redis readers for task execution logs."""

from __future__ import annotations

from dataclasses import dataclass, field

from antcode_core.application.services.logs.postgres_log_service import (
    PostgresLogEntry,
    postgres_log_service,
)
from antcode_core.common.config import settings

MAX_LOG_READ_BYTES = 32 * 1024 * 1024
MAX_LOG_READ_ENTRIES = 10_000
LOG_READ_PAGE_SIZE = 32
LOG_TRUNCATED_MARKER = "\n... (log truncated to fit response size budget)"


@dataclass
class BoundedLogCollector:
    """Collect decoded log lines without exceeding one response budget."""

    max_bytes: int = MAX_LOG_READ_BYTES
    max_entries: int = MAX_LOG_READ_ENTRIES
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    bytes_used: int = 0
    entries_seen: int = 0
    truncated: bool = False
    _last_target: str = "stdout"

    def add(self, log_type: str, content: str) -> bool:
        """Add one line and return whether the caller may continue reading."""
        if self.entries_seen >= self.max_entries:
            self.truncated = True
            return False
        target_name = "stderr" if log_type.lower() == "stderr" else "stdout"
        target = self.stderr if target_name == "stderr" else self.stdout
        self._last_target = target_name
        self.entries_seen += 1
        separator_bytes = 1 if target else 0
        remaining = self.max_bytes - self.bytes_used - separator_bytes
        encoded = content.encode("utf-8")
        if len(encoded) <= remaining:
            target.append(content)
            self.bytes_used += len(encoded) + separator_bytes
            return True
        if remaining > 0:
            target.append(encoded[:remaining].decode("utf-8", "ignore"))
            self.bytes_used += remaining + separator_bytes
        self.truncated = True
        return False

    def texts(self) -> tuple[str, str]:
        """Render stdout/stderr and attach the truncation marker once."""
        stdout = list(self.stdout)
        stderr = list(self.stderr)
        if self.truncated:
            target = stderr if self._last_target == "stderr" else stdout
            target.append(LOG_TRUNCATED_MARKER)
        return "\n".join(stdout), "\n".join(stderr)


async def read_postgres_execution_logs(run_id: str) -> tuple[str, str, bool]:
    """Read one stable execution-log snapshot within the response budget."""
    collector = BoundedLogCollector()
    snapshot_id = await postgres_log_service.latest_snapshot_id(run_id)
    after: int | None = None
    while snapshot_id > 0:
        page = await postgres_log_service.list_history_window_page(
            run_id,
            limit=LOG_READ_PAGE_SIZE,
            snapshot_id=snapshot_id,
            lower=1,
            after=after,
        )
        if not page:
            break
        if not _collect_page(collector, page):
            break
        after = _page_storage_id(page[-1])
        if len(page) < LOG_READ_PAGE_SIZE:
            break
    output, error = collector.texts()
    return output, error, collector.entries_seen > 0


async def read_postgres_log_text(run_id: str, log_type: str, lines: int | None) -> str:
    """Read one log type; a positive line limit returns the newest N lines."""
    collector = BoundedLogCollector(max_entries=_entry_limit(lines))
    snapshot_id = await postgres_log_service.latest_snapshot_id(run_id)
    if snapshot_id <= 0:
        return ""
    if lines and lines > 0:
        await _collect_latest_type(
            collector,
            run_id=run_id,
            log_type=log_type,
            snapshot_id=snapshot_id,
            line_limit=int(lines),
        )
        collector.stdout.reverse()
        collector.stderr.reverse()
    else:
        await _collect_history_type(
            collector,
            run_id=run_id,
            log_type=log_type,
            snapshot_id=snapshot_id,
        )
    return collector.texts()[1 if log_type == "stderr" else 0]


async def read_redis_execution_logs(run_id: str) -> tuple[str, str]:
    """Read Redis ingest streams incrementally within the same response budget."""
    if not settings.REDIS_URL:
        return "", ""
    redis, stream_keys = await _redis_log_sources(run_id)
    collector = BoundedLogCollector()
    for stream_key in stream_keys:
        if not await _read_redis_stream(
            redis,
            stream_key=stream_key,
            run_id=run_id,
            collector=collector,
        ):
            break
    return collector.texts()


async def _collect_history_type(
    collector: BoundedLogCollector,
    *,
    run_id: str,
    log_type: str,
    snapshot_id: int,
) -> None:
    after: int | None = None
    while True:
        page = await postgres_log_service.list_history_window_page(
            run_id,
            limit=LOG_READ_PAGE_SIZE,
            snapshot_id=snapshot_id,
            lower=1,
            after=after,
        )
        if not page or not _collect_page(collector, page, log_type=log_type):
            return
        after = _page_storage_id(page[-1])
        if len(page) < LOG_READ_PAGE_SIZE:
            return


async def _collect_latest_type(
    collector: BoundedLogCollector,
    *,
    run_id: str,
    log_type: str,
    snapshot_id: int,
    line_limit: int,
) -> None:
    before: int | None = None
    while True:
        page = await postgres_log_service.list_latest_page(
            run_id,
            limit=LOG_READ_PAGE_SIZE,
            snapshot_id=snapshot_id,
            before=before,
        )
        if not page or not _collect_page(
            collector,
            page,
            log_type=log_type,
            line_limit=line_limit,
        ):
            return
        before = _page_storage_id(page[-1])
        if len(page) < LOG_READ_PAGE_SIZE:
            return


def _collect_page(
    collector: BoundedLogCollector,
    page: list[PostgresLogEntry],
    *,
    log_type: str | None = None,
    line_limit: int | None = None,
) -> bool:
    for entry in page:
        normalized_type = (entry.log_type or "stdout").lower()
        if log_type is not None and normalized_type != log_type:
            continue
        if not collector.add(normalized_type, entry.content):
            return False
        if line_limit is not None and collector.entries_seen >= line_limit:
            return False
    return True


def _entry_limit(lines: int | None) -> int:
    if lines is None or lines <= 0:
        return MAX_LOG_READ_ENTRIES
    return min(int(lines), MAX_LOG_READ_ENTRIES)


def _page_storage_id(entry: PostgresLogEntry) -> int:
    if entry.storage_id <= 0:
        raise RuntimeError("task log keyset page returned an invalid storage_id")
    return entry.storage_id


async def _redis_log_sources(run_id: str):
    from antcode_core.infrastructure.redis.client import get_redis_client
    from antcode_core.infrastructure.redis.control_plane import log_ingest_stream_key
    from antcode_core.infrastructure.redis.keys import RedisKeys

    redis = await get_redis_client()
    keys = RedisKeys(settings.REDIS_NAMESPACE)
    # ingest stream key 必须走 log_ingest_stream_key：写入侧（gateway
    # LogHandler / Direct log_ingest_fence / master ingest loop）都用它，
    # 而它带 Cluster hash tag（``{ns}:log:ingest``）。这里手拼 f"{ns}:log:ingest"
    # 会得到一个永远没人写的键，PG 未 flush 窗口内的日志回落恒为空。
    return redis, [keys.log_stream_key(run_id), log_ingest_stream_key(settings.REDIS_NAMESPACE)]


async def _read_redis_stream(
    redis,
    *,
    stream_key: str,
    run_id: str,
    collector: BoundedLogCollector,
) -> bool:
    last_id = "0-0"
    while True:
        result = await redis.xread({stream_key: last_id}, count=LOG_READ_PAGE_SIZE)
        if not result or not result[0][1]:
            return True
        for msg_id, fields in result[0][1]:
            last_id = _decode_redis_value(msg_id)
            for log_type, content in decode_stream_message(fields, run_id):
                if content and not collector.add(log_type, content):
                    return False


def decode_stream_message(fields: dict, run_id_filter: str) -> list[tuple[str, str]]:
    """Decode one protobuf or legacy Redis log message."""
    proto_present = b"p" in fields or "p" in fields
    if proto_present:
        proto_raw = fields[b"p"] if b"p" in fields else fields["p"]
        return _decode_protobuf_message(proto_raw, run_id_filter)
    decoded = _decode_legacy_log(fields)
    msg_run_id = fields.get(b"run_id") or fields.get("run_id") or ""
    if isinstance(msg_run_id, bytes):
        msg_run_id = msg_run_id.decode("utf-8")
    if run_id_filter and msg_run_id and msg_run_id != run_id_filter:
        return []
    return [(decoded["log_type"] or "stdout", decoded["content"] or "")]


def _decode_protobuf_message(proto_raw, run_id_filter: str) -> list[tuple[str, str]]:
    try:
        from antcode_contracts import data_pb2

        if isinstance(proto_raw, str):
            proto_raw = proto_raw.encode("latin-1")
        batch = data_pb2.LogBatch()
        batch.ParseFromString(proto_raw)
        return [
            _decode_proto_entry(entry, run_id_filter)
            for entry in batch.entries
            if not run_id_filter or entry.run_id == run_id_filter
        ]
    except Exception as exc:
        raise ValueError(f"日志 Stream protobuf 解码失败: run_id={run_id_filter}") from exc


def _decode_proto_entry(entry, run_id_filter: str) -> tuple[str, str]:
    del run_id_filter
    from antcode_contracts import data_pb2

    name = data_pb2.LogType.Name(entry.log_type)
    log_type = name.removeprefix("LOG_TYPE_").lower() if name.startswith("LOG_TYPE_") else name.lower()
    return log_type, entry.content or ""


def _decode_legacy_log(fields: dict) -> dict[str, str]:
    def get_field(name: str):
        return fields.get(name) or fields.get(name.encode("utf-8"))

    return {
        "log_type": _decode_redis_value(get_field("log_type")),
        "content": _decode_redis_value(get_field("content")),
    }


def _decode_redis_value(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value) if value is not None else ""
