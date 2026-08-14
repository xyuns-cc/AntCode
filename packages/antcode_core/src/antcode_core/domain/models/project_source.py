"""Project source binding models."""

from typing import TYPE_CHECKING

from tortoise import fields

from antcode_core.domain.models.base import BaseModel


class ProjectSource(BaseModel):
    """One project mapped to one Git repository subdirectory."""

    project_id = fields.BigIntField(unique=True)
    repository_id = fields.BigIntField(db_index=True)
    ref = fields.CharField(max_length=255, default="main")
    subdir = fields.CharField(max_length=500)
    include_paths: fields.JSONField[list[str]] = fields.JSONField(default=list)
    if TYPE_CHECKING:
        resolved_commit: str | None
    else:
        resolved_commit = fields.CharField(max_length=64, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "project_sources"
        indexes = [
            ("project_id",),
            ("repository_id", "subdir"),
            ("repository_id", "ref"),
        ]


__all__ = ["ProjectSource"]
