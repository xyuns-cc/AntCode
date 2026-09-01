"""E2E clients for log queries and ticket-authenticated SSE log streams."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode

import httpx

from .helpers import API_PREFIX, extract_data, request_json

DEFAULT_STREAM_TIMEOUT_SECONDS = 15.0
# run 进入终态与 Worker stdout 可从日志 API 查到之间存在固有异步窗口，两段都排在结果
# 上报之后：Worker 侧 BatchSender 按 batch_timeout=1s 攒批
# （services/worker/src/antcode_worker/logs/batch.py），master 侧 log_ingest_loop 以 1s
# block 消费 Stream（services/master/src/antcode_master/ingester/log_ingest_loop.py）。
# 所以"终态即日志可查"不是系统提供的保证，查询侧必须显式等到它成立，超时后连原文一起
# 失败——零等待断言的是一个并不存在的 happens-before。
DEFAULT_QUERY_TIMEOUT_SECONDS = 30.0
DEFAULT_QUERY_INTERVAL_SECONDS = 0.5
HISTORY_END_TYPES = frozenset({"historical_logs_end", "no_historical_logs"})
# 实时帧的两个来源：Redis ingest 路径（direct/gateway worker）source='realtime'，
# Worker HTTP 上报路径（distributed_log_service → SSELogNotifier）source='task_execution'
REALTIME_SOURCES = frozenset({"realtime", "task_execution"})


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


async def wait_for_raw_log_content(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    token: str,
    expected_content: str,
    timeout: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
    interval: float = DEFAULT_QUERY_INTERVAL_SECONDS,
) -> str:
    """轮询 raw 日志直到 Worker stdout 落库；超时即带最后一次原文失败。"""
    deadline = time.monotonic() + timeout
    content = ""
    while time.monotonic() < deadline:
        payload = await get_logs(client, token, run_id, log_format="raw")
        content = str(payload.get("raw_content", ""))
        if expected_content in content:
            return content
        await asyncio.sleep(interval)
    raise AssertionError(f"raw 日志在 {timeout}s 内始终不含 {expected_content!r}: {content!r}")


async def issue_stream_ticket(client: httpx.AsyncClient, token: str, run_id: str) -> str:
    payload = await request_json(
        client,
        "POST",
        "/logs/stream-ticket",
        token=token,
        params={"run_id": run_id},
    )
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
        is_realtime = data.get("source") in REALTIME_SOURCES
        if message_type == "log_line" and expected_content in str(data.get("content", "")):
            if history_complete and (not realtime_only or is_realtime):
                return message
    raise AssertionError(f"SSE 未收到目标日志 {expected_content!r}: observed_types={observed_types}")


async def scan_for_realtime_log_and_status(
    messages: AsyncIterator[dict[str, Any]],
    expected_content: str,
    *,
    terminal_statuses: frozenset[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Require a realtime log and terminal status on the same SSE connection."""
    history_complete = False
    target_log: dict[str, Any] | None = None
    terminal_status: dict[str, Any] | None = None
    async for message in messages:
        message_type = str(message.get("type"))
        if message_type in HISTORY_END_TYPES:
            history_complete = True
            continue
        data = message.get("data") or {}
        if history_complete and message_type == "log_line":
            realtime = data.get("source") in REALTIME_SOURCES
            if realtime and expected_content in str(data.get("content", "")):
                target_log = message
        if history_complete and message_type == "run_status":
            if str(data.get("status") or "") in terminal_statuses:
                terminal_status = message
        if target_log is not None and terminal_status is not None:
            return target_log, terminal_status
    raise AssertionError(f"SSE 未同时收到实时日志和终态: content={expected_content!r}")


async def _wait_for_stream_log(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    token: str,
    expected_content: str,
    timeout: float,
    realtime_only: bool,
) -> dict[str, Any]:
    ticket = await issue_stream_ticket(client, token, run_id)
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


async def wait_for_realtime_log_and_status(
    client: httpx.AsyncClient,
    run_id: str,
    *,
    token: str,
    expected_content: str,
    terminal_statuses: frozenset[str],
    timeout: float = DEFAULT_STREAM_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ticket = await issue_stream_ticket(client, token, run_id)
    path = build_stream_path(run_id, ticket)
    try:
        async with asyncio.timeout(timeout):
            async with client.stream("GET", path) as response:
                response.raise_for_status()
                return await scan_for_realtime_log_and_status(
                    parse_sse_messages(response.aiter_lines()),
                    expected_content,
                    terminal_statuses=terminal_statuses,
                )
    except TimeoutError as exc:
        raise AssertionError(f"SSE 等待实时日志和终态超时({timeout}s): {expected_content!r}") from exc
