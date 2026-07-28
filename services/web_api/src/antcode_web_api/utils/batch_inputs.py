"""Validation for management endpoints that accept identifier batches."""

from __future__ import annotations

from typing import Any

from antcode_core.common.config import settings
from fastapi import HTTPException, status


def bounded_distinct_ids(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 必须是数组")
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 不能为空")
    limit = settings.API_MANAGEMENT_BATCH_MAX_ITEMS
    if len(value) > limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} 单次上限 {limit} 项，当前 {len(value)} 项",
        )
    unique: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 包含非法 ID")
        normalized = str(item).strip()
        if not normalized:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 包含空 ID")
        if normalized in seen:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} 包含重复 ID")
        seen.add(normalized)
        unique.append(normalized)
    return unique


__all__ = ["bounded_distinct_ids"]
