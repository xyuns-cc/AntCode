"""P1-SSE-02: 历史计划期扫描的小块物化 + 字节预算截断回归。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.ingest_history as history_module
import pytest
from antcode_web_api.streams.ingest_history import IngestLogHistoryReader

# 单次 DB 物化行数的上界契约：回归防止悄悄回到 200 行大页。
_MAX_PLAN_CHUNK_ROWS = 25
_SNAPSHOT_ID = 1000
_BYTE_BUDGET = 10


async def _checkpoint() -> None:
    return None


def _entry(sequence: int) -> SimpleNamespace:
    return SimpleNamespace(
        log_type="stdout",
        content=str(sequence),
        timestamp=None,
        sequence=sequence,
        event_id=f"42-0:{sequence}",
        storage_id=sequence,
    )


@pytest.mark.asyncio
async def test_plan_latest_materializes_small_chunks_and_stops_at_byte_budget(monkeypatch):
    # P1-SSE-02: 计划期"最新历史"扫描先物化后记预算，单次物化必须是小块
    # （HISTORY_FETCH_CHUNK_ROWS）；字节预算耗尽后不得再发起下一次取数。
    limits: list[int] = []

    async def latest(_run_id, *, limit, snapshot_id, before=None):
        limits.append(limit)
        top = before - 1 if before else snapshot_id
        bottom = max(1, top - limit + 1)
        return [_entry(sequence) for sequence in range(top, bottom - 1, -1)]

    monkeypatch.setattr(history_module.postgres_log_service, "latest_snapshot_id", AsyncMock(return_value=_SNAPSHOT_ID))
    monkeypatch.setattr(history_module.postgres_log_service, "list_latest_page", latest)
    reader = IngestLogHistoryReader("ac")

    window = await reader.plan_latest(
        "run-1", max_lines=10_000, max_bytes=_BYTE_BUDGET, size_of=lambda item: 1, checkpoint=_checkpoint
    )

    assert history_module.HISTORY_FETCH_CHUNK_ROWS <= _MAX_PLAN_CHUNK_ROWS
    assert limits == [history_module.HISTORY_FETCH_CHUNK_ROWS]
    assert (window.sent_lines, window.truncated) == (_BYTE_BUDGET, True)
