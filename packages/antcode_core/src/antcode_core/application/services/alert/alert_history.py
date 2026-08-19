"""进程内告警历史缓冲与聚合。

从 alert_service 拆出：发送编排与"最近发了什么"的环形缓冲是两件事，且
alert_service.py 已顶到 300 行硬上限。
"""

from __future__ import annotations

MAX_HISTORY_RECORDS = 1000
DEFAULT_HISTORY_LIMIT = 50


class AlertHistory:
    """最近若干条告警记录（仅本进程内存，重启即丢）。"""

    def __init__(self, max_records: int = MAX_HISTORY_RECORDS) -> None:
        self._records: list[dict] = []
        self._max_records = max_records

    def __len__(self) -> int:
        return len(self._records)

    def add(self, record: dict) -> None:
        self._records.append(record)
        if len(self._records) > self._max_records:
            self._records.pop(0)

    def mark_last_status(self, status: str) -> None:
        """把投递结果回填到刚落库的那条记录上。"""
        if self._records:
            self._records[-1]["status"] = status

    def recent(self, *, limit: int = DEFAULT_HISTORY_LIMIT, level=None, source=None) -> list[dict]:
        records = self._records
        if level:
            records = [item for item in records if item.get("level") == level]
        if source:
            records = [item for item in records if item.get("source") == source]
        return list(reversed(records[-limit:]))

    def counts_by(self, field: str, *, unknown: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._records:
            key = record.get(field, unknown)
            counts[key] = counts.get(key, 0) + 1
        return counts


__all__ = ["DEFAULT_HISTORY_LIMIT", "MAX_HISTORY_RECORDS", "AlertHistory"]
