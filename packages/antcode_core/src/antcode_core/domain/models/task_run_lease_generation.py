"""Persisted TaskRun lease generations used to validate in-flight log backlog."""

from tortoise import fields
from tortoise.indexes import Index

from antcode_core.common.redis_stream_id import MAX_STREAM_ID_LENGTH
from antcode_core.domain.models.base import StrictFieldsModel


class TaskRunLeaseGeneration(StrictFieldsModel):
    id = fields.BigIntField(primary_key=True)
    run_id = fields.CharField(max_length=64)
    worker_id = fields.BigIntField()
    lease_id = fields.CharField(max_length=64)
    lease_gen = fields.BigIntField(null=True)
    log_valid_through_id = fields.CharField(max_length=MAX_STREAM_ID_LENGTH, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    closed_at = fields.DatetimeField(null=True)

    class Meta:
        table = "task_run_lease_generations"
        unique_together = (("run_id", "lease_id"),)
        indexes = (Index(fields=("run_id", "worker_id", "lease_id"), name="idx_task_run_lease_generation_lookup"),)


__all__ = ["TaskRunLeaseGeneration"]
