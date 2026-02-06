"""
用户模型

用户账户的数据模型定义。
"""

from passlib.context import CryptContext
from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id

pwd_context = CryptContext(
    schemes=["bcrypt_sha256", "bcrypt"],
    deprecated="auto",
)


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
        try:
            return pwd_context.verify(password, self.password_hash)
        except ValueError:
            return False

    def __str__(self):
        return self.username


__all__ = [
    "User",
    "pwd_context",
]
