"""Crawl queue dead-letter payload minimization."""

from __future__ import annotations

from dataclasses import replace

from antcode_core.application.services.crawl.backends.base import QueueTask

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
        "api-key",
    }
)


def dead_letter_payload(task: QueueTask, reason: str) -> dict:
    safe_headers = {
        str(name): value for name, value in task.headers.items() if str(name).strip().lower() not in SENSITIVE_HEADERS
    }
    failed = replace(task, msg_id="", status="failed", headers=safe_headers)
    return {
        **failed.to_dict(),
        "original_priority": task.priority,
        "dead_letter_reason": reason,
    }


def invalid_message_payload(msg_id: str, data: dict, error: Exception) -> dict:
    return {
        "source_message_id": msg_id,
        "url": data.get("url") if isinstance(data.get("url"), str) else "",
        "batch_id": data.get("batch_id") if isinstance(data.get("batch_id"), str) else "",
        "project_id": data.get("project_id") if isinstance(data.get("project_id"), str) else "",
        "status": "failed",
        "dead_letter_reason": f"invalid_queue_task:{type(error).__name__}",
    }
