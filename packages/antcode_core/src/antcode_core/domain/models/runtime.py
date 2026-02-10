"""
运行时环境模型

跨语言运行时与解释器的数据模型定义。
"""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id
from antcode_core.domain.models.enums import InterpreterSource, RuntimeKind, RuntimeScope


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

    使用统一运行时抽象（runtime_kind + runtime_locator），
    语言特有字段通过 runtime_details 扩展。
    """

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    runtime_kind = fields.CharEnumField(RuntimeKind, default=RuntimeKind.PYTHON)
    scope = fields.CharEnumField(RuntimeScope)
    key = fields.CharField(max_length=100, null=True)
    version = fields.CharField(max_length=20)
    runtime_locator = fields.CharField(max_length=500, unique=True)
    runtime_details = fields.JSONField(default=dict)

    # 关联解释器（应用层维护关联，不使用数据库外键）
    interpreter_id = fields.BigIntField(
        null=True,
        db_index=True,
        description="关联的解释器 ID（Python 运行时可用）",
    )

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
        table = "runtimes"
        indexes = [
            ("runtime_kind",),
            ("scope", "key"),
            ("version",),
            ("created_at",),
            ("created_by",),
            ("runtime_kind", "scope", "version"),
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
    runtime_id = fields.BigIntField(db_index=True, description="关联的运行时 ID")
    is_current = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    created_by = fields.BigIntField(null=True)

    class Meta:
        table = "project_runtime_bindings"
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
