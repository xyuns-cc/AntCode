from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, urlencode

from .api import AntCodeApi
from .config import LoadSettings
from .runner import OperationResult

HISTORY_END_TYPES = frozenset({"historical_logs_end", "no_historical_logs"})
LOG_MESSAGE_TYPE = "log_line"


async def read_sse_history(
    api: AntCodeApi,
    settings: LoadSettings,
    run_id: str,
    *,
    index: int,
) -> OperationResult[int]:
    """建立 ticket 鉴权的 SSE 日志流并完整消费历史回放。"""
    ticket = await _issue_ticket(api, run_id, index)
    path = _stream_path(run_id, ticket)
    async with api.stream("GET", path, index) as response:
        response.raise_for_status()
        lines = await asyncio.wait_for(
            _count_history(_parse_sse_data(response.aiter_lines())),
            timeout=settings.timeout_seconds,
        )
    return OperationResult(response.status_code, lines)


async def _issue_ticket(api: AntCodeApi, run_id: str, index: int) -> str:
    payload = await api.require_json(
        "POST",
        f"/logs/stream-ticket?{urlencode({'run_id': run_id})}",
        index,
    )
    data = payload.get("data")
    ticket = data.get("ticket") if isinstance(data, dict) else None
    if not isinstance(ticket, str) or not ticket:
        raise ValueError("stream ticket response is malformed")
    return ticket


def _stream_path(run_id: str, ticket: str) -> str:
    return f"/logs/runs/{quote(run_id, safe='')}/stream?ticket={quote(ticket, safe='')}"


async def _parse_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """最小 SSE 解析：取 ``data:`` 行 JSON，空行分帧，跳过注释。"""
    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if line == "":
            if data_lines:
                yield json.loads("\n".join(data_lines))
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").removeprefix(" "))


async def _count_history(messages: AsyncIterator[dict[str, Any]]) -> int:
    """计数历史回放的 log_line，并校验与服务端 sent_lines 汇总一致。"""
    lines = 0
    async for payload in messages:
        message_type = payload.get("type") if isinstance(payload, dict) else None
        if message_type == "stream_error":
            raise RuntimeError(f"SSE returned error: {payload.get('message')}")
        if message_type == LOG_MESSAGE_TYPE:
            lines += 1
        if message_type in HISTORY_END_TYPES:
            sent_lines = payload.get("sent_lines")
            if not isinstance(sent_lines, int) or sent_lines < 0:
                raise ValueError("SSE history summary has invalid sent_lines")
            if sent_lines != lines:
                raise RuntimeError(f"SSE history count mismatch: received={lines}, sent_lines={sent_lines}")
            return lines
    raise RuntimeError("SSE stream ended before history completed")
