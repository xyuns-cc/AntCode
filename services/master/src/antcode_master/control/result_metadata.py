"""TaskRun 结果元数据合并。"""

from __future__ import annotations

from typing import Any


def merge_result_data(
    current: dict[str, Any] | None,
    update: dict[str, Any] | None,
) -> dict[str, Any]:
    """保留创建期/重试元数据，并用本阶段结果更新同名字段。"""
    merged = dict(current or {})
    merged.update(update or {})
    return merged


__all__ = ["merge_result_data"]
