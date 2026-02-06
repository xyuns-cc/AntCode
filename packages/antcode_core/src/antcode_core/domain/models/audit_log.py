"""
审计日志模型

系统操作审计记录的数据模型定义。
"""

from tortoise import fields
from tortoise.models import Model

from antcode_core.domain.models.enums import AuditAction


class AuditLog(Model):
    """审计日志

    记录系统中的重要操作，用于安全审计和问题追踪。
    """

    id = fields.IntField(primary_key=True)

    # 操作信息
    action = fields.CharEnumField(AuditAction, description="操作类型")
    resource_type = fields.CharField(max_length=50, description="资源类型")
    resource_id = fields.CharField(max_length=100, null=True, description="资源ID")
    resource_name = fields.CharField(max_length=200, null=True, description="资源名称")

    # 操作者信息
    user_id = fields.IntField(null=True, description="用户ID")
    username = fields.CharField(max_length=100, description="用户名")
    ip_address = fields.CharField(max_length=50, null=True, description="IP地址")
    user_agent = fields.CharField(max_length=500, null=True, description="User-Agent")

    # 操作详情
    description = fields.TextField(null=True, description="操作描述")
    old_value = fields.JSONField(null=True, description="修改前的值")
    new_value = fields.JSONField(null=True, description="修改后的值")

    # 结果
    success = fields.BooleanField(default=True, description="是否成功")
    error_message = fields.TextField(null=True, description="错误信息")

    # 时间戳
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")

    class Meta:
        table = "audit_logs"
        table_description = "审计日志"
        ordering = ["-created_at"]
        indexes = [
            ("action",),
            ("user_id",),
            ("username",),
            ("resource_type",),
            ("created_at",),
            # 复合索引用于统计查询
            ("created_at", "action"),
            ("created_at", "username"),
            ("created_at", "resource_type"),
            ("created_at", "success"),
        ]


__all__ = [
    "AuditLog",
]
