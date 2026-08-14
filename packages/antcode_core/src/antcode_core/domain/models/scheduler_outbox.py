"""Transactional outbox for scheduler and crawl control events."""

from typing import Any

from tortoise import fields

from antcode_core.common.error_message_field import PersistedErrorMessageField
from antcode_core.domain.models.base import BaseModel


class SchedulerOutbox(BaseModel):
    """A durable event committed in the same transaction as domain state."""

    event_type = fields.CharField(max_length=64)
    aggregate_type = fields.CharField(max_length=32)
    aggregate_id = fields.CharField(max_length=64)
    payload: fields.JSONField[dict[str, Any]] = fields.JSONField()
    attempts = fields.IntField(default=0)
    # P2 §4.5: 消费侧重投计数与发布侧 attempts 分离 —— 共用一列时发布阶段
    # 曾退避多次的事件，消费侧首次失败即可能触顶终止。
    consume_attempts = fields.IntField(default=0)
    available_at = fields.DatetimeField()
    published_at = fields.DatetimeField(null=True)
    last_error = PersistedErrorMessageField(null=True)
    consumed_at = fields.DatetimeField(null=True)
    consume_owner = fields.CharField(max_length=128, null=True)
    consume_started_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "scheduler_outbox"
        indexes = [
            ("published_at", "available_at", "id"),
            ("aggregate_type", "aggregate_id"),
            ("consumed_at", "consume_started_at"),
        ]


__all__ = ["SchedulerOutbox"]
