"""SSE 日志流测试共享构造器。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_web_api.streams.log_stream_service as svc_module
from antcode_web_api.streams.ingest_history import PostgresHistoryWindow
from antcode_web_api.streams.log_stream_gap import GapScanProgress, GapWindow
from antcode_web_api.streams.log_stream_service import LogStreamService

from tests.unit.web_api.fake_stream_capacity import make_broker


def parse_frame(frame: bytes) -> tuple[str, dict]:
    text = frame.decode("utf-8")
    assert text.endswith("\n\n")
    lines = text.strip().split("\n")
    event_line = next(line for line in lines if line.startswith("event: "))
    data_line = next(line for line in lines if line.startswith("data: "))
    assert event_line.startswith("event: ")
    assert data_line.startswith("data: ")
    return event_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))


def parse_frame_id(frame: bytes) -> str | None:
    return next(
        (line.removeprefix("id: ") for line in frame.decode("utf-8").splitlines() if line.startswith("id: ")),
        None,
    )


def history_entry(
    log_type: str,
    content: str,
    sequence: int | None,
    *,
    event_id: str | None = None,
) -> dict:
    entry = {
        "log_type": log_type,
        "content": content,
        "timestamp": "",
        "sequence": sequence,
        "source": "pg_history",
    }
    return {**entry, "event_id": event_id} if event_id else entry


class HistoryReader:
    def __init__(self, entries: list[dict], *, error: Exception | None = None) -> None:
        self.entries = entries
        self.error = error
        self.selected: list[dict] = []
        self.snapshot_id = 0

    async def plan_latest(self, run_id, *, max_lines, max_bytes, size_of, checkpoint):
        if self.error is not None:
            raise self.error
        total_bytes = 0
        newest_first: list[dict] = []
        truncated = False
        for entry in reversed(self.entries):
            size = size_of(entry)
            if len(newest_first) >= max_lines or total_bytes + size > max_bytes:
                truncated = True
                break
            newest_first.append(entry)
            total_bytes += size
        self.selected = list(reversed(newest_first))
        if self.snapshot_id > 0:
            return PostgresHistoryWindow(
                run_id,
                self.snapshot_id,
                None,
                len(self.selected),
                total_bytes,
                truncated,
            )
        return SimpleNamespace(truncated=truncated)

    async def iter_window(self, _window, *, checkpoint):
        for entry in self.selected:
            await checkpoint()
            yield entry


class GapReader:
    """新版 keyset gap reader 协议的假实现（plan 只取快照，预算在迭代期）。

    ``lines_per_pass`` 模拟行数预算截断：单轮最多产出该行数，剩余行标记
    ``progress.truncated`` 留给下一轮（P1-SSE-02/04 语义）。
    """

    def __init__(
        self,
        entries: list[dict] | None = None,
        *,
        snapshot_id: int = 0,
        lines_per_pass: int | None = None,
    ) -> None:
        self.entries = entries or []
        self.snapshot_id = snapshot_id
        self.lines_per_pass = lines_per_pass
        self.after_ids: list[int] = []
        self.error: Exception | None = None

    async def plan(self, run_id: str, after_id: int) -> GapWindow:
        self.after_ids.append(after_id)
        if self.error is not None:
            raise self.error
        return GapWindow(run_id, after_id, self.snapshot_id)

    async def iter_window(self, window: GapWindow, progress: GapScanProgress):
        emitted = 0
        selected = [entry for entry in self.entries if window.after_id < entry["storage_id"] <= window.snapshot_id]
        for entry in selected:
            progress.last_id = entry["storage_id"]
            yield entry
            emitted += 1
            if self.lines_per_pass is not None and emitted >= self.lines_per_pass:
                progress.truncated = emitted < len(selected)
                return


def setup_stream(
    monkeypatch,
    history: list[dict],
    status: str = "running",
    *,
    gap_reader: GapReader | None = None,
):
    broker = make_broker()
    reader = HistoryReader(history)
    follower = SimpleNamespace(
        follow=AsyncMock(),
        unfollow=AsyncMock(),
        history_reader=reader,
    )
    monkeypatch.setattr(svc_module, "run_stream_broker", broker)
    monkeypatch.setattr(svc_module, "ingest_log_follower", follower)
    execution = SimpleNamespace(status=SimpleNamespace(value=status))
    generator = LogStreamService(gap_reader or GapReader()).stream(
        "run-1",
        execution,
        user_id=7,
        session_jti="jti",
    )
    return broker, follower, generator


async def drain_until_history_end(generator) -> list[tuple[str, dict]]:
    """消费 run_status -> historical_logs_start -> log_line* -> 历史结束帧。"""
    frames = [parse_frame(await anext(generator))]
    assert frames[0][0] == "run_status"
    frames.append(parse_frame(await anext(generator)))
    assert frames[1][0] == "historical_logs_start"
    while True:
        event, data = parse_frame(await anext(generator))
        frames.append((event, data))
        if event in {"historical_logs_end", "no_historical_logs"}:
            return frames
