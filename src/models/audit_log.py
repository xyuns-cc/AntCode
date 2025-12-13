"""审计日志数据模型"""

from tortoise import fields
from tortoise.models import Model
from enum import Enum


class AuditAction(str, Enum):
    """审计操作类型"""
    # 用户相关
    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    PASSWORD_CHANGE = "password_change"

    # 用户管理
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DELETE = "user_delete"
    USER_ROLE_CHANGE = "user_role_change"

    # 项目相关
    PROJECT_CREATE = "project_create"
    PROJECT_UPDATE = "project_update"
    PROJECT_DELETE = "project_delete"

    # 任务相关
    TASK_CREATE = "task_create"
    TASK_UPDATE = "task_update"
    TASK_DELETE = "task_delete"
    TASK_EXECUTE = "task_execute"
    TASK_STOP = "task_stop"

    # 节点相关
    NODE_CREATE = "node_create"
    NODE_UPDATE = "node_update"
    NODE_DELETE = "node_delete"
    NODE_RESOURCE_UPDATE = "node_resource_update"

    # 系统配置
    CONFIG_UPDATE = "config_update"
    ALERT_CONFIG_UPDATE = "alert_config_update"

    # 环境管理
    ENV_CREATE = "env_create"
    ENV_DELETE = "env_delete"

    # 其他
    EXPORT_DATA = "export_data"
    IMPORT_DATA = "import_data"


class AuditLog(Model):
    """审计日志"""

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
        ]
