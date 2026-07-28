"""PostgreSQL entry construction for distributed worker logs."""

from __future__ import annotations

from datetime import datetime

from antcode_core.application.services.logs.postgres_log_service import PostgresLogEntry


def entries_for_lines(
    run_id: str,
    log_type: str,
    lines: list[str],
    *,
    sequences: list[int],
    timestamp: datetime,
) -> list[PostgresLogEntry]:
    level = "ERROR" if log_type == "stderr" else "INFO"
    return [
        PostgresLogEntry(
            run_id=run_id,
            log_type=log_type,
            content=line,
            sequence=sequence,
            timestamp=timestamp,
            level=level,
            source="worker_report",
        )
        for line, sequence in zip(lines, sequences, strict=True)
    ]


__all__ = ["entries_for_lines"]
