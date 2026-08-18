"""PostgreSQL authority epoch for Master scheduler writes."""

from tortoise import fields

from antcode_core.domain.models.base import StrictFieldsModel

# 单行表的主键值。Master 写入端与所有派发入口的读取端都必须用同一个名字，
# 因此常量定义在模型侧共享，不允许各层各写一份字面量。
SCHEDULER_AUTHORITY_NAME = "master"


class SchedulerAuthority(StrictFieldsModel):
    """Single-row authority epoch advanced on every Leader activation."""

    name = fields.CharField(max_length=32, primary_key=True)
    fencing_token = fields.BigIntField()
    activated_at = fields.DatetimeField()

    class Meta:
        table = "scheduler_authority"


__all__ = ["SCHEDULER_AUTHORITY_NAME", "SchedulerAuthority"]
