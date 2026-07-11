"""Strict database batch update helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger
from tortoise import Model


@dataclass(frozen=True)
class BulkUpdateOptions:
    key_field: str = "id"
    batch_size: int = 100


@dataclass(frozen=True)
class _BatchUpdatePlan:
    key_field: str
    fields: list[str]


class DatabaseOptimizer:
    """Execute bounded database batch updates without fallback writes."""

    @staticmethod
    async def bulk_update(
        model_class: type[Model],
        updates: list[dict[str, Any]],
        options: BulkUpdateOptions = BulkUpdateOptions(),
    ) -> int:
        if options.batch_size <= 0:
            raise ValueError("batch_size 必须大于 0")
        plan = _BatchUpdatePlan(options.key_field, _updated_fields(updates, options.key_field))
        updated_count = 0
        for start in range(0, len(updates), options.batch_size):
            batch = updates[start : start + options.batch_size]
            updated_count += await _update_batch(model_class, batch, plan)
        logger.info(f"批量更新完成: 更新 {updated_count} 个对象")
        return updated_count


def _updated_fields(updates: list[dict[str, Any]], key_field: str) -> list[str]:
    return sorted({field for update in updates for field in update if field != key_field})


async def _update_batch(
    model_class: type[Model],
    updates: list[dict[str, Any]],
    plan: _BatchUpdatePlan,
) -> int:
    keys = [update[plan.key_field] for update in updates if plan.key_field in update]
    if not keys or not plan.fields:
        return 0
    objects = await model_class.filter(**{f"{plan.key_field}__in": keys}).all()
    objects_by_key = {getattr(obj, plan.key_field): obj for obj in objects}
    changed = _apply_updates(objects_by_key, updates, plan.key_field)
    if not changed:
        return 0
    await model_class.bulk_update(changed, fields=plan.fields)
    return len(changed)


def _apply_updates(
    objects_by_key: dict[Any, Model],
    updates: list[dict[str, Any]],
    key_field: str,
) -> list[Model]:
    changed: list[Model] = []
    for update in updates:
        obj = objects_by_key.get(update.get(key_field))
        if obj is None:
            continue
        for field, value in update.items():
            if field != key_field:
                setattr(obj, field, value)
        changed.append(obj)
    return changed


__all__ = ["BulkUpdateOptions", "DatabaseOptimizer"]
