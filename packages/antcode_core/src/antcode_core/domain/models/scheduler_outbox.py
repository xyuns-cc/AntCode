"""Transactional outbox for scheduler and crawl control events."""

from typing import Any

from tortoise import fields

from antcode_core.domain.models.base import BaseModel


class SchedulerOutbox(BaseModel):
    """A durable event committed in the same transaction as domain state."""

    event_type = fields.CharField(max_length=64)
    aggregate_type = fields.CharField(max_length=32)
    aggregate_id = fields.CharField(max_length=64)
    payload: fields.JSONField[dict[str, Any]] = fields.JSONField()
    attempts = fields.IntField(default=0)
    available_at = fields.DatetimeField()
    published_at = fields.DatetimeField(null=True)
    last_error = fields.TextField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "scheduler_outbox"
        indexes = [
            ("published_at", "available_at", "id"),
            ("aggregate_type", "aggregate_id"),
        ]


__all__ = ["SchedulerOutbox"]
