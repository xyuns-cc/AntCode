"""
UserSession model (P1-09):
- 服务端 session 表, 存 refresh_token 的 jti
- 支持全量撤销 (改密/离职/泄漏)
- ``revoked_at`` 字段供 ``verify_refresh_token`` 检查

数据库迁移
----------
本 model 新增了 ``user_sessions`` 表, 上线时**必须**创建对应的 aerich
migration(或在开发环境走 ``scripts/init_db.py`` 的
``Tortoise.generate_schemas(safe=True)`` 建表)。

字段索引说明
-------------
- ``jti`` unique: verify_refresh_token 走 jti 精确查询
- ``user_id`` index: revoke_all_sessions 需要批量筛用户 session
- ``(user_id, revoked_at)`` 组合: 查用户当前活跃 session 列表
- ``expires_at`` index: 后续可加定时清理过期记录的 job
"""

from tortoise import fields, models


class UserSession(models.Model):
    """用户会话记录(refresh token jti)

    P1-09: refresh token 从纯 JWT 升级为 JWT + 服务端 jti 记录, 支持:
    - 单个 session 撤销(登出)
    - 用户全量撤销(改密/离职) via ``UserService.revoke_all_sessions``
    - refresh token 泄漏时即时失效, 无需等 exp 到期
    """

    id = fields.IntField(primary_key=True)
    user_id = fields.BigIntField(db_index=True, description="所属用户 User.id")
    jti = fields.CharField(max_length=64, unique=True, description="refresh token JWT ID (uuid4.hex, 32 chars)")
    created_at = fields.DatetimeField(auto_now_add=True)
    expires_at = fields.DatetimeField(db_index=True, description="refresh token 过期时间 (清理用)")
    revoked_at = fields.DatetimeField(null=True, description="P1-09: 撤销时间戳; 非空即视为已失效")
    device_info = fields.CharField(max_length=256, null=True, description="UA/IP 摘要 (可选, 用于登录审计)")

    class Meta:
        table = "user_sessions"
        indexes = [
            ("user_id", "revoked_at"),  # 查用户活跃 session
        ]

    def __str__(self) -> str:
        state = "revoked" if self.revoked_at else "active"
        return f"UserSession<user={self.user_id} jti={self.jti[:8]}… {state}>"
