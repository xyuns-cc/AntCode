"""SSE 帧协议：格式化与消息构造。

事件类型与原 WebSocket 消息一一对应（``type`` 字段沿用），仅两处差异：
- 服务端业务错误用 ``stream_error``（原 ``error``），避开 EventSource 内建
  的网络层 error 事件——前端 ``onerror`` 只管重连，``stream_error`` 只管展示；
- 新增 ``ping`` 心跳事件（原生 EventSource 看不到 SSE 注释行，心跳必须是
  真实事件，前端才能做探活 watchdog）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def format_sse_event(event: str, data: dict[str, Any] | None = None) -> bytes:
    """构造一帧 SSE：``event: <type>\\ndata: <json>\\n\\n``。

    ``data`` 单行 JSON 序列化（ensure_ascii=False），不含裸换行，
    单个 ``data:`` 行即完整帧。
    """
    payload = json.dumps(data or {}, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_log_line_message(
    run_id: str,
    *,
    log_type: str,
    content: str,
    timestamp: str | None,
    sequence: int | None,
    source: str,
) -> dict[str, Any]:
    """log_line 消息体（与原 WS 消息结构一致，``data.sequence`` 为新增字段）。"""
    return {
        "type": "log_line",
        "run_id": run_id,
        "data": {
            "run_id": run_id,
            "log_type": log_type or "stdout",
            "content": content or "",
            "timestamp": timestamp or _now_iso(),
            "level": "ERROR" if log_type == "stderr" else "INFO",
            "source": source,
            "sequence": sequence,
        },
        "timestamp": _now_iso(),
    }


def build_run_status_message(
    run_id: str,
    *,
    status: str,
    progress: float | None,
    message: str,
) -> dict[str, Any]:
    return {
        "type": "run_status",
        "run_id": run_id,
        "data": {
            "status": status,
            "progress": progress,
            "message": message,
        },
        "timestamp": _now_iso(),
    }


def build_ping_message() -> dict[str, Any]:
    return {"type": "ping", "timestamp": _now_iso()}


def build_stream_error_message(message: str) -> dict[str, Any]:
    return {"type": "stream_error", "message": message, "timestamp": _now_iso()}


def build_history_complete_message(sent: int) -> dict[str, Any]:
    """历史回放结束帧：有内容 -> historical_logs_end，无内容 -> no_historical_logs。"""
    return {
        "type": "historical_logs_end" if sent > 0 else "no_historical_logs",
        "sent_lines": sent,
        "timestamp": _now_iso(),
    }


def normalize_sequence(value: Any) -> int | None:
    """把 proto/JSON/PG 各来源的 sequence 归一为 int；无效/缺失归 None。

    注意 sequence=0 是合法值（worker 端计数器从 0 起），调用方做重叠过滤时
    不能用真值判断。
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
