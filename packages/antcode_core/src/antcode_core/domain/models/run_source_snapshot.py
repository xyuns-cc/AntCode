"""Run source snapshot model."""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel


class RunSourceSnapshot(BaseModel):
    """Immutable source metadata captured for one task run."""

    run_id = fields.CharField(max_length=128, db_index=True)
    project_id = fields.BigIntField(db_index=True)
    repository_id = fields.BigIntField(db_index=True)
    artifact_id = fields.BigIntField(db_index=True)
    artifact_sha256 = fields.CharField(max_length=64, db_index=True)
    resolved_commit = fields.CharField(max_length=64, db_index=True)
    subdir = fields.CharField(max_length=500)
    entry_point = fields.CharField(max_length=255)
    include_paths = fields.JSONField(default=list)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "run_source_snapshots"
        unique_together = (("run_id", "project_id"),)
        indexes = [
            ("run_id",),
            ("project_id", "created_at"),
            ("artifact_sha256",),
        ]


__all__ = ["RunSourceSnapshot"]
