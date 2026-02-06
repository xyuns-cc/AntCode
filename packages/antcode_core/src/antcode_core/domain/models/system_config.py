"""
系统配置模型

动态系统配置的数据模型定义。
"""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel


class SystemConfig(BaseModel):
    """系统配置模型

    用于存储动态配置项，支持运行时修改。
    """

    # 配置键（唯一标识）
    config_key = fields.CharField(max_length=100, unique=True, db_index=True)

    # 配置值（JSON字符串）
    config_value = fields.TextField()

    # 配置分类（用于分组展示）
    category = fields.CharField(max_length=50, db_index=True)

    # 配置描述
    description = fields.TextField(null=True)

    # 数据类型（string, int, float, bool, json）
    value_type = fields.CharField(max_length=20, default="string")

    # 是否启用
    is_active = fields.BooleanField(default=True)

    # 最后修改人
    modified_by = fields.CharField(max_length=50, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "system_configs"
        indexes = [
            ("config_key",),
            ("category",),
            ("is_active",),
            ("category", "is_active"),
        ]

    def __str__(self):
        return f"{self.config_key}: {self.config_value}"


__all__ = [
    "SystemConfig",
]
