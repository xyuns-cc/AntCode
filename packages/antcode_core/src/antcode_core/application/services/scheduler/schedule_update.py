"""Normalize task schedule updates while preserving one active trigger field."""

from __future__ import annotations

from typing import Any

from antcode_core.domain.models.enums import ScheduleType

TRIGGER_CONFIG_FIELDS = ("cron_expression", "interval_seconds", "scheduled_time")
_TRIGGER_FIELD_BY_TYPE = {
    ScheduleType.CRON: "cron_expression",
    ScheduleType.INTERVAL: "interval_seconds",
    ScheduleType.DATE: "scheduled_time",
    ScheduleType.ONCE: "scheduled_time",
}
_REQUIRED_TRIGGER_FIELD_BY_TYPE = {
    ScheduleType.CRON: "cron_expression",
    ScheduleType.INTERVAL: "interval_seconds",
    ScheduleType.DATE: "scheduled_time",
}


def normalize_trigger_update(task: Any, update_data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    normalized = dict(update_data)
    current_type = ScheduleType(task.schedule_type)
    target_type = ScheduleType(normalized.get("schedule_type", current_type))
    active_field = _TRIGGER_FIELD_BY_TYPE[target_type]
    incompatible = [field for field in TRIGGER_CONFIG_FIELDS if field != active_field and field in normalized]
    if incompatible:
        raise ValueError(f"{target_type.value.upper()} 调度不接受字段: {', '.join(incompatible)}")
    required_field = _REQUIRED_TRIGGER_FIELD_BY_TYPE.get(target_type)
    type_changed = target_type != current_type
    if type_changed and required_field and required_field not in normalized:
        raise ValueError(f"{target_type.value.upper()} 调度必须提供 {required_field}")
    if required_field and not normalized.get(required_field, getattr(task, required_field, None)):
        raise ValueError(f"{target_type.value.upper()} 调度必须提供 {required_field}")
    active_changed = active_field in normalized and normalized[active_field] != getattr(task, active_field, None)
    for field in TRIGGER_CONFIG_FIELDS:
        if field != active_field:
            normalized[field] = None
    trigger_changed = type_changed or active_changed
    if trigger_changed:
        normalized["next_run_time"] = None
    return normalized, trigger_changed


__all__ = ["TRIGGER_CONFIG_FIELDS", "normalize_trigger_update"]
