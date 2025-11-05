"""
环境与解释器相关数据模型
包含解释器、虚拟环境、以及项目与虚拟环境的绑定
"""

from tortoise import fields
from tortoise.models import Model

from .enums import VenvScope, InterpreterSource


class Interpreter(Model):
    """语言解释器（当前仅支持 Python）"""
    id = fields.BigIntField(pk=True)
    tool = fields.CharField(max_length=20, default="python", description="工具/语言，如 python")
    version = fields.CharField(max_length=20, description="版本号")
    install_dir = fields.CharField(max_length=500, description="安装目录")
    python_bin = fields.CharField(max_length=500, description="解释器可执行路径")
    status = fields.CharField(max_length=20, default="installed", description="状态")
    source = fields.CharEnumField(InterpreterSource, default=InterpreterSource.MISE, description="来源：mise/local")

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    created_by = fields.BigIntField(null=True, description="创建者ID")

    class Meta:
        table = "interpreters"
        unique_together = ("tool", "version", "source")


class Venv(Model):
    """虚拟环境记录（私有/共享）"""
    id = fields.BigIntField(pk=True)
    scope = fields.CharEnumField(VenvScope, description="作用域：shared/private")
    key = fields.CharField(max_length=100, null=True, description="共享环境标识")
    version = fields.CharField(max_length=20, description="Python版本")
    venv_path = fields.CharField(max_length=500, unique=True, description="虚拟环境路径")

    interpreter: fields.ForeignKeyRelation[Interpreter] = fields.ForeignKeyField(
        "models.Interpreter", related_name="venvs", on_delete=fields.RESTRICT
    )

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    created_by = fields.BigIntField(null=True, description="创建者ID")

    class Meta:
        table = "venvs"
        indexes = [("scope", "key"), ("version",)]


class ProjectVenvBinding(Model):
    """项目与虚拟环境绑定（支持历史与当前）"""
    id = fields.BigIntField(pk=True)
    project_id = fields.BigIntField(description="项目ID")
    venv: fields.ForeignKeyRelation[Venv] = fields.ForeignKeyField(
        "models.Venv", related_name="bindings", on_delete=fields.CASCADE
    )
    is_current = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    created_by = fields.BigIntField(null=True, description="创建者ID")

    class Meta:
        table = "project_venv_bindings"
        indexes = [("project_id", "is_current")]
