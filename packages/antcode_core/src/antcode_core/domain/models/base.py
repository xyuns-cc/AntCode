"""
基础模型

提供所有模型的基类和通用工具函数。
"""

import uuid

from tortoise import fields
from tortoise.models import Model


def generate_public_id() -> str:
    """生成公开ID（不带连字符的UUID）"""
    return uuid.uuid4().hex


class BaseModel(Model):
    """带有 public_id 的基础模型

    所有业务模型都应继承此类，提供：
    - 自增主键 id
    - 唯一公开标识 public_id（用于 API 暴露）
    - 通过 public_id 查询的便捷方法
    """

    id = fields.BigIntField(primary_key=True)
    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )

    class Meta:
        abstract = True

    @classmethod
    async def get_by_public_id(cls, public_id: str):
        """通过 public_id 获取对象"""
        return await cls.get_or_none(public_id=public_id)

    @classmethod
    async def filter_by_public_ids(cls, public_ids: list[str]):
        """通过 public_id 列表过滤"""
        return await cls.filter(public_id__in=public_ids)


class TimestampMixin:
    """时间戳混入

    提供 created_at 和 updated_at 字段。
    """

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)


class SoftDeleteMixin:
    """软删除混入

    提供 is_deleted 和 deleted_at 字段。
    """

    is_deleted = fields.BooleanField(default=False)
    deleted_at = fields.DatetimeField(null=True)


__all__ = [
    "generate_public_id",
    "BaseModel",
    "TimestampMixin",
    "SoftDeleteMixin",
]
