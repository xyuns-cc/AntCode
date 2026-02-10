"""
用户模型

用户账户的数据模型定义。
"""

import bcrypt
from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id


class BcryptPasswordContext:
    """基于 bcrypt 的密码上下文。"""

    def hash(self, password: str) -> str:
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        return hashed.decode("utf-8")

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except (TypeError, ValueError):
            return False


pwd_context = BcryptPasswordContext()


class User(BaseModel):
    """用户模型

    表示系统用户账户。
    """

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    username = fields.CharField(max_length=50, unique=True)
    password_hash = fields.CharField(max_length=128)
    email = fields.CharField(max_length=100, null=True)
    is_active = fields.BooleanField(default=True)
    is_admin = fields.BooleanField(default=False)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)
    last_login_at = fields.DatetimeField(null=True)

    class Meta:
        table = "users"
        indexes = [
            ("username",),
            ("email",),
            ("is_active",),
            ("is_admin",),
            ("last_login_at",),
            ("is_active", "is_admin"),
            ("public_id",),
        ]

    def set_password(self, password: str) -> None:
        """设置密码"""
        self.password_hash = pwd_context.hash(password)

    def verify_password(self, password: str) -> bool:
        """验证密码"""
        return pwd_context.verify(password, self.password_hash)

    def __str__(self):
        return self.username


__all__ = [
    "User",
    "pwd_context",
]
