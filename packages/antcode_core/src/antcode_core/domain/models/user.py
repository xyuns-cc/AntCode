"""
用户模型

用户账户的数据模型定义。
"""

import secrets
from enum import StrEnum

import bcrypt
from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id

BCRYPT_MAX_PASSWORD_BYTES = 72


def password_byte_length(password: str) -> int:
    return len(password.encode("utf-8"))


def password_fits_bcrypt(password: str) -> bool:
    return password_byte_length(password) <= BCRYPT_MAX_PASSWORD_BYTES


class UserRole(StrEnum):
    """用户角色"""

    USER = "user"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


class BcryptPasswordContext:
    """基于 bcrypt 的密码上下文。"""

    def hash(self, password: str) -> str:
        if password_byte_length(password) > BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError("密码 UTF-8 编码不能超过 72 字节")
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        return hashed.decode("utf-8")

    def verify(self, password: str, password_hash: str) -> bool:
        if password_byte_length(password) > BCRYPT_MAX_PASSWORD_BYTES:
            return False
        try:
            return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except (TypeError, ValueError):
            return False


pwd_context = BcryptPasswordContext()
_DUMMY_PASSWORD_HASH = pwd_context.hash(secrets.token_urlsafe(32))


def consume_dummy_password_check(password: str) -> None:
    """为不存在的用户执行等成本校验，收敛用户名计时侧信道。"""
    pwd_context.verify(password, _DUMMY_PASSWORD_HASH)


class User(BaseModel):
    """用户模型

    表示系统用户账户。
    """

    public_id = fields.CharField(max_length=32, unique=True, default=generate_public_id)
    username = fields.CharField(max_length=50, unique=True)
    password_hash = fields.CharField(max_length=128)
    email = fields.CharField(max_length=100, null=True)
    is_active = fields.BooleanField(default=True)
    is_admin = fields.BooleanField(default=False)
    role = fields.CharEnumField(UserRole, default=UserRole.USER, max_length=20)
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
        ]

    def set_password(self, password: str) -> None:
        """设置密码"""
        self.password_hash = pwd_context.hash(password)

    def verify_password(self, password: str) -> bool:
        """验证密码"""
        return pwd_context.verify(password, self.password_hash)

    @property
    def is_super_admin(self) -> bool:
        """是否为超级管理员"""
        return self.role == UserRole.SUPER_ADMIN

    def _sync_admin_flag(self) -> None:
        """以 ``role`` 为单一权威，自动派生 ``is_admin`` 布尔字段。

        历史代码大量直接读 ``user.is_admin``；新代码应优先走 ``role`` /
        ``require_role``。本方法保证两者永不漂移。
        """
        derived = self.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)
        if self.is_admin != derived:
            self.is_admin = derived

    async def save(self, *args, **kwargs):  # type: ignore[override]
        self._sync_admin_flag()
        return await super().save(*args, **kwargs)

    def __str__(self):
        return self.username


__all__ = [
    "User",
    "UserRole",
    "BCRYPT_MAX_PASSWORD_BYTES",
    "consume_dummy_password_check",
    "password_byte_length",
    "password_fits_bcrypt",
    "pwd_context",
]
