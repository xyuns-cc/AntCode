"""PostgreSQL source artifact models."""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel


class SourceArtifact(BaseModel):
    """Immutable source bundle metadata stored in PostgreSQL."""

    content_hash = fields.CharField(max_length=64, unique=True, db_index=True)
    media_type = fields.CharField(max_length=128)
    size_bytes = fields.BigIntField()
    chunk_count = fields.IntField()
    repository_id = fields.BigIntField(null=True, db_index=True)
    resolved_commit = fields.CharField(max_length=64, null=True, db_index=True)
    source_subdir = fields.CharField(max_length=500, null=True)
    include_paths_hash = fields.CharField(max_length=64, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "source_artifacts"
        indexes = [
            ("content_hash",),
            ("repository_id", "resolved_commit"),
            ("created_at",),
        ]


class SourceArtifactChunk(BaseModel):
    """Ordered binary chunk for a source artifact."""

    artifact_id = fields.BigIntField(db_index=True)
    chunk_index = fields.IntField()
    content = fields.BinaryField()
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "source_artifact_chunks"
        unique_together = (("artifact_id", "chunk_index"),)
        indexes = [
            ("artifact_id", "chunk_index"),
        ]


__all__ = ["SourceArtifact", "SourceArtifactChunk"]
