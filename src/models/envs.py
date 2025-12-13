"""环境和解释器模型"""

from tortoise import fields

from src.models.base import BaseModel, generate_public_id
from src.models.enums import VenvScope, InterpreterSource


class Interpreter(BaseModel):
    """语言解释器模型"""
    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    tool = fields.CharField(max_length=20, default="python")
    version = fields.CharField(max_length=20)
    install_dir = fields.CharField(max_length=500)
    python_bin = fields.CharField(max_length=500)
    status = fields.CharField(max_length=20, default="installed")
    source = fields.CharEnumField(InterpreterSource, default=InterpreterSource.MISE)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    created_by = fields.BigIntField(null=True)

    class Meta:
        table = "interpreters"
        unique_together = ("tool", "version", "source")
        indexes = [
            ("tool",),
            ("status",),
            ("created_at",),
            ("tool", "status"),
            ("tool", "version"),
            ("public_id",),
        ]


class Venv(BaseModel):
    """虚拟环境模型"""
    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    scope = fields.CharEnumField(VenvScope)
    key = fields.CharField(max_length=100, null=True)
    version = fields.CharField(max_length=20)
    venv_path = fields.CharField(max_length=500, unique=True)

    interpreter = fields.ForeignKeyField(
        "models.Interpreter", related_name="venvs", on_delete=fields.RESTRICT
    )

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    created_by = fields.BigIntField(null=True)

    # 分布式节点关联
    node_id = fields.BigIntField(null=True, db_index=True, description="所属节点ID")

    class Meta:
        table = "venvs"
        indexes = [
            ("scope", "key"),
            ("version",),
            ("created_at",),
            ("created_by",),
            ("scope", "version"),
            ("public_id",),
            ("node_id",),
            ("node_id", "scope"),
        ]


class ProjectVenvBinding(BaseModel):
    """项目与虚拟环境绑定"""
    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)
    project_id = fields.BigIntField()
    venv = fields.ForeignKeyField(
        "models.Venv", related_name="bindings", on_delete=fields.CASCADE
    )
    is_current = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    created_by = fields.BigIntField(null=True)

    class Meta:
        table = "project_venv_bindings"
        indexes = [
            ("project_id", "is_current"),
            ("venv_id", "is_current"),
            ("created_at",),
            ("created_by",),
            ("public_id",),
        ]
