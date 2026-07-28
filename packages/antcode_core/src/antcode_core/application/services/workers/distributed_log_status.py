"""Pure status formatting helpers for distributed task logs."""

from __future__ import annotations

from typing import Any

TERMINAL_STATUSES = {"success", "failed", "timeout", "cancelled", "skipped", "rejected"}


def status_log_message(
    status: str,
    exit_code: int | None,
    error_message: str | None,
) -> str:
    message = f"[STATUS] 任务状态更新: {status}"
    if exit_code is not None:
        message = f"{message}, 退出码: {exit_code}"
    if error_message:
        message = f"{message}, 错误: {error_message}"
    return message


def display_status_message(status: dict[str, Any]) -> str:
    value = str(status["status"]).lower()
    if value == "running":
        return "任务开始执行"
    if value == "success":
        return "任务执行成功"
    if value == "failed":
        return f"任务执行失败: {status.get('error_message') or '未知错误'}"
    if value == "timeout":
        return "任务执行超时"
    if value == "cancelled":
        return "任务已取消"
    return f"任务状态: {value}"


def status_progress(status: str) -> float | None:
    return 100.0 if status.lower() in TERMINAL_STATUSES else None


__all__ = [
    "TERMINAL_STATUSES",
    "display_status_message",
    "status_log_message",
    "status_progress",
]
