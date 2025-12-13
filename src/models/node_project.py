"""节点项目绑定模型 - 分布式同步状态追踪"""

from tortoise import fields
from src.models.base import BaseModel, generate_public_id


class NodeProject(BaseModel):
    """节点项目绑定 - 追踪项目版本与分发状态"""
    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)

    node_id = fields.BigIntField(db_index=True, description="节点ID")
    project_id = fields.BigIntField(db_index=True, description="项目ID")
    project_public_id = fields.CharField(max_length=32, db_index=True, description="项目公开ID")

    node_local_project_id = fields.CharField(max_length=50, null=True, description="节点本地ID")

    file_hash = fields.CharField(max_length=64, description="文件hash")
    file_size = fields.BigIntField(description="文件大小")

    transfer_method = fields.CharField(
        max_length=20, 
        description="传输方式"
    )
    synced_at = fields.DatetimeField(auto_now_add=True, description="同步时间")
    updated_at = fields.DatetimeField(auto_now=True, description="更新时间")

    status = fields.CharField(
        max_length=20, 
        default="synced",
        description="同步状态"
    )

    sync_count = fields.IntField(default=1, description="同步次数")
    last_used_at = fields.DatetimeField(null=True, description="使用时间")

    metadata = fields.JSONField(null=True, description="元数据")

    class Meta:
        table = "node_projects"
        unique_together = [("node_id", "project_public_id")]
        indexes = [
            ("node_id",),
            ("project_id",),
            ("project_public_id",),
            ("node_id", "project_public_id"),
            ("node_id", "status"),
            ("status",),
            ("synced_at",),
            ("last_used_at",),
            ("public_id",),
        ]

    def __str__(self):
        return f"节点{self.node_id}@{self.project_public_id}[{self.status}]"


class NodeProjectFile(BaseModel):
    """项目文件追踪 - 文件级增量同步"""
    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)

    node_project_id = fields.BigIntField(db_index=True, description="项目绑定ID")

    file_path = fields.CharField(max_length=500, description="文件路径")
    file_hash = fields.CharField(max_length=64, description="文件hash")
    file_size = fields.IntField(description="文件大小")

    synced_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "node_project_files"
        unique_together = [("node_project_id", "file_path")]
        indexes = [
            ("node_project_id",),
            ("file_path",),
            ("file_hash",),
            ("public_id",),
        ]

    def __str__(self):
        return f"{self.file_path}[{self.file_hash[:8]}]"

