"""PostgreSQL source artifact models."""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel


class SourceArtifact(BaseModel):
    """Immutable source bundle metadata stored in PostgreSQL."""

    content_hash = fields.CharField(max_length=64, unique=True, db_index=True)
    media_type = fields.CharField(max_length=128)
    size_bytes = fields.BigIntField()
    chunk_count = fields.IntField()
    # 刻意不是外键、也刻意不随仓库删除级联：artifact 按 content_hash 寻址
    # （见 infrastructure/postgres/artifact_store.py，所有查询都只用 content_hash，
    # 没有一处按 repository_id 查）。内容相同的 bundle 全局只有这一行，仓库只是
    # 它的来源标注而非所有者——删仓库就删它会砸掉其他仓库的缓存命中，更会让
    # run_source_snapshots 引用的历史 run 源码快照永久失效。
    # 回收由 artifact_cleanup_service 的 TTL GC 负责，且带"无 snapshot 引用"
    # 这个正确的安全条件。删仓后残留的 repository_id 是一个无人读的陈旧标注。
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
