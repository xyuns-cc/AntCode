"""环境和解释器模型"""

from tortoise import fields
from tortoise.models import Model

from src.models.enums import VenvScope, InterpreterSource


class Interpreter(Model):
    """语言解释器模型"""
    id = fields.BigIntField(pk=True)
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
        ]


class Venv(Model):
    """虚拟环境模型"""
    id = fields.BigIntField(pk=True)
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

    class Meta:
        table = "venvs"
        indexes = [
            ("scope", "key"),
            ("version",),
            ("created_at",),
            ("created_by",),
            ("scope", "version"),
        ]


class ProjectVenvBinding(Model):
    """项目与虚拟环境绑定"""
    id = fields.BigIntField(pk=True)
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
        ]
