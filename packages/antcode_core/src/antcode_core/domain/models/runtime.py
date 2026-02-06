"""
运行时环境模型

Python 解释器和虚拟环境的数据模型定义。
"""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id
from antcode_core.domain.models.enums import InterpreterSource, RuntimeScope


class Interpreter(BaseModel):
    """语言解释器模型

    表示一个 Python 解释器安装。
    """

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
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


class Runtime(BaseModel):
    """运行时环境模型

    表示一个 Python 虚拟环境。
    """

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    scope = fields.CharEnumField(RuntimeScope)
    key = fields.CharField(max_length=100, null=True)
    version = fields.CharField(max_length=20)
    venv_path = fields.CharField(max_length=500, unique=True)

    # 关联解释器（应用层维护关联，不使用数据库外键）
    interpreter_id = fields.BigIntField(db_index=True, description="关联的解释器 ID")

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    created_by = fields.BigIntField(null=True)

    # 分布式节点关联
    worker_id = fields.BigIntField(
        null=True,
        db_index=True,
        description="所属 Worker ID",
    )

    class Meta:
        table = "venvs"
        indexes = [
            ("scope", "key"),
            ("version",),
            ("created_at",),
            ("created_by",),
            ("scope", "version"),
            ("public_id",),
            ("worker_id",),
            ("worker_id", "scope"),
        ]


class ProjectRuntimeBinding(BaseModel):
    """项目与运行时环境绑定"""

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    project_id = fields.BigIntField()
    # 关联运行时（应用层维护关联，不使用数据库外键）
    runtime_id = fields.BigIntField(db_index=True, description="关联的运行时 ID")
    is_current = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    created_by = fields.BigIntField(null=True)

    class Meta:
        table = "project_venv_bindings"
        indexes = [
            ("project_id", "is_current"),
            ("runtime_id", "is_current"),
            ("created_at",),
            ("created_by",),
            ("public_id",),
        ]


__all__ = [
    "Interpreter",
    "Runtime",
    "ProjectRuntimeBinding",
]
