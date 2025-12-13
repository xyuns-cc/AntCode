"""基础模型"""
import uuid

from tortoise import fields
from tortoise.models import Model


def generate_public_id():
    """生成公开ID（不带连字符的UUID）"""
    return uuid.uuid4().hex


class BaseModel(Model):
    """带有public_id的基础模型"""

    id = fields.BigIntField(primary_key=True)
    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id, db_index=True)

    class Meta:
        abstract = True

    @classmethod
    async def get_by_public_id(cls, public_id: str):
        """通过public_id获取对象"""
        return await cls.get_or_none(public_id=public_id)

    @classmethod
    async def filter_by_public_ids(cls, public_ids: list):
        """通过public_id列表过滤"""
        return await cls.filter(public_id__in=public_ids)


class TimestampMixin:
    """时间戳混入"""
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
