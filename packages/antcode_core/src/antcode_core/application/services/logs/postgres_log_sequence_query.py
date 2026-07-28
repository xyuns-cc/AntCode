"""Read HTTP-reported logs back with database-authoritative identities."""

from __future__ import annotations

from collections.abc import Sequence

from antcode_core.application.services.logs.postgres_log_service import (
    TASK_LOG_COLUMN_NAMES,
    PostgresLogEntry,
    row_to_log_entry,
)


async def list_persisted_log_sequences(
    entries: Sequence[PostgresLogEntry],
) -> list[PostgresLogEntry]:
    """Return one homogeneous persisted batch in its original sequence order."""
    requested = tuple(entries)
    if not requested:
        return []
    run_id, log_type = _batch_identity(requested)
    requested_by_sequence = _requested_entries(requested)

    from antcode_core.domain.models.task_log import TaskLog

    rows = await TaskLog.filter(
        run_id=run_id,
        log_type=log_type,
        sequence__in=tuple(requested_by_sequence),
    ).values(*TASK_LOG_COLUMN_NAMES)
    persisted_by_sequence = _validate_rows(rows or [], requested_by_sequence)
    return [persisted_by_sequence[entry.sequence] for entry in requested]


def _batch_identity(entries: tuple[PostgresLogEntry, ...]) -> tuple[str, str]:
    first = entries[0]
    if not first.run_id or not first.log_type:
        raise ValueError("日志回读批次缺少 run_id 或 log_type")
    identity = (first.run_id, first.log_type)
    if any((entry.run_id, entry.log_type) != identity for entry in entries):
        raise ValueError("日志回读批次必须具有相同 run_id 和 log_type")
    return identity


def _requested_entries(
    entries: tuple[PostgresLogEntry, ...],
) -> dict[int, PostgresLogEntry]:
    sequences = [entry.sequence for entry in entries]
    if any(sequence <= 0 for sequence in sequences):
        raise ValueError("日志回读 sequence 必须为正整数")
    if len(set(sequences)) != len(sequences):
        raise ValueError("日志回读批次 sequence 不得重复")
    return {entry.sequence: entry for entry in entries}


def _validate_rows(
    rows: list[dict],
    requested: dict[int, PostgresLogEntry],
) -> dict[int, PostgresLogEntry]:
    persisted = [_to_entry(row) for row in rows]
    sequences = [entry.sequence for entry in persisted]
    if len(set(sequences)) != len(sequences):
        raise RuntimeError("日志写入后回读到重复 sequence")
    if len(persisted) != len(requested):
        raise RuntimeError("日志写入后回读数量与请求不一致")
    if set(sequences) != set(requested):
        raise RuntimeError("日志写入后回读 sequence 与请求不一致")
    for entry in persisted:
        expected = requested[entry.sequence]
        if not _matches_request(entry, expected):
            raise RuntimeError(f"日志写入后回读字段与请求不一致: sequence={entry.sequence}")
    return {entry.sequence: entry for entry in persisted}


def _matches_request(actual: PostgresLogEntry, expected: PostgresLogEntry) -> bool:
    return (
        actual.run_id == expected.run_id
        and actual.log_type == expected.log_type
        and actual.content == expected.content
        and actual.timestamp == expected.timestamp
        and actual.level == expected.level
        and actual.source == expected.source
        and actual.event_id == expected.event_id
    )


def _to_entry(row: dict) -> PostgresLogEntry:
    entry = row_to_log_entry(row)
    # 本模块的额外校验：回读行必须带数据库权威 storage_id。
    if entry.storage_id <= 0:
        raise RuntimeError("日志写入后回读结果缺少有效 storage_id")
    return entry


__all__ = ["list_persisted_log_sequences"]
