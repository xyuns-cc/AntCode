"""E2E clients for log queries and ticket-authenticated SSE log streams."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import httpx

from .helpers import API_PREFIX, extract_data, request_json

DEFAULT_STREAM_TIMEOUT_SECONDS = 15.0
HISTORY_END_TYPES = frozenset({"historical_logs_end", "no_historical_logs"})


async def get_logs(
    client: httpx.AsyncClient,
    token: str,
    run_id: str,
    *,
    log_format: str,
) -> dict[str, Any]:
    payload = await request_json(
        client,
        "GET",
        f"/logs/runs/{run_id}",
        token=token,
        params={"format": log_format},
    )
    return extract_data(payload) or {}


async def issue_stream_ticket(client: httpx.AsyncClient, token: str) -> str:
    payload = await request_json(client, "POST", "/logs/stream-ticket", token=token)
    data = extract_data(payload) or {}
    ticket = data.get("ticket")
    assert ticket, "日志流 ticket 签发失败"
    return str(ticket)


def build_stream_path(run_id: str, ticket: str) -> str:
    """SSE 流路径：仅携带一次性 ticket，JWT 永不进 URL。"""
    query = urlencode({"ticket": ticket})
    return f"{API_PREFIX}/logs/runs/{run_id}/stream?{query}"


async def parse_sse_messages(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """最小 SSE 解析器：按空行分帧，取 ``data:`` 行 JSON。

    服务端每帧 data 都是完整消息对象（含 ``type`` 字段），event 名与
    type 一致，因此只需解析 data。
    """
    data_lines: list[str] = []
    async for raw_line in lines:
        line = raw_line.rstrip("\r\n") if raw_line.endswith(("\r", "\n")) else raw_line
        if line == "":
            if data_lines:
                yield json.loads("\n".join(data_lines))
            data_lines = []
            continue
        if line.startswith(":"):
            continue  # SSE 注释（keep-alive 等）
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").removeprefix(" "))


async def scan_for_target_log(
    messages: AsyncIterator[dict[str, Any]],
    expected_content: str,
    *,
    realtime_only: bool,
) -> dict[str, Any]:
    """在消息流中等待目标日志；realtime_only 时忽略历史回放中的命中。"""
    observed_types: list[str] = []
    history_complete = not realtime_only
    async for message in messages:
        message_type = str(message.get("type"))
        observed_types.append(message_type)
        if message_type in HISTORY_END_TYPES:
            history_complete = True
            continue
        data = message.get("data") or {}
        is_realtime = data.get("source") == "realtime"
        if message_type == "log_line" and expected_content in str(data.get("content", "")):
            if history_complete and (not realtime_only or is_realtime):
                return message
    raise AssertionError(f"SSE 未收到目标日志 {expected_content!r}: observed_types={observed_types}")


async def _wait_for_stream_log(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    token: str,
    expected_content: str,
    timeout: float,
    realtime_only: bool,
) -> dict[str, Any]:
    ticket = await issue_stream_ticket(client, token)
    path = build_stream_path(run_id, ticket)
    try:
        async with asyncio.timeout(timeout):
            async with client.stream("GET", path) as response:
                response.raise_for_status()
                return await scan_for_target_log(
                    parse_sse_messages(response.aiter_lines()),
                    expected_content,
                    realtime_only=realtime_only,
                )
    except TimeoutError as exc:
        raise AssertionError(f"SSE 等待目标日志超时({timeout}s): {expected_content!r}") from exc


async def wait_for_stream_log(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    token: str,
    expected_content: str,
    timeout: float = DEFAULT_STREAM_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    return await _wait_for_stream_log(
        client,
        run_id,
        token=token,
        expected_content=expected_content,
        timeout=timeout,
        realtime_only=False,
    )


async def wait_for_realtime_stream_log(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    token: str,
    expected_content: str,
    timeout: float = DEFAULT_STREAM_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    return await _wait_for_stream_log(
        client,
        run_id,
        token=token,
        expected_content=expected_content,
        timeout=timeout,
        realtime_only=True,
    )
