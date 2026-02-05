"""用户会话模型

用于：
- 记录用户的在线状态（基于会话 last_seen_at）
- 支持超级管理员踢下线（撤销 web 会话）

说明：
- web：浏览器/前端登录产生的会话
- node：节点与 Master 通信使用的会话（不计入用户在线）
- service：Master 内部调用节点/任务分发等使用的会话（不计入用户在线）
"""

from tortoise import fields

from src.models.base import BaseModel


class UserSession(BaseModel):
    """用户会话"""

    user = fields.ForeignKeyField("models.User", related_name="sessions", on_delete=fields.CASCADE)

    # web / node / service
    session_type = fields.CharField(max_length=20, db_index=True)

    # node 会话可绑定 node public_id，用于稳定复用 token
    node_id = fields.CharField(max_length=32, null=True, db_index=True)

    ip_address = fields.CharField(max_length=50, null=True)
    user_agent = fields.CharField(max_length=500, null=True)

    last_seen_at = fields.DatetimeField(null=True, db_index=True)
    revoked_at = fields.DatetimeField(null=True, db_index=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "user_sessions"
        indexes = [
            ("user_id", "session_type"),
            ("session_type", "last_seen_at"),
            ("node_id", "session_type"),
        ]

