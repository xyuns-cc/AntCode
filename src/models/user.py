"""
用户模型定义 - 纯数据模型，不包含业务逻辑
"""
from passlib.context import CryptContext
from tortoise import fields
from tortoise.models import Model

# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class User(Model):
    """用户模型 - 仅包含数据定义和基础操作"""

    id = fields.IntField(pk=True, description="用户ID")
    username = fields.CharField(max_length=50, unique=True, description="用户名")
    password_hash = fields.CharField(max_length=128, description="密码哈希")
    email = fields.CharField(max_length=100, null=True, description="邮箱")
    is_active = fields.BooleanField(default=True, description="是否激活")
    is_admin = fields.BooleanField(default=False, description="是否管理员")
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")
    updated_at = fields.DatetimeField(auto_now=True, description="更新时间")
    last_login_at = fields.DatetimeField(null=True, description="最后登录时间")

    class Meta:
        table = "users"
        table_description = "用户表"

    def set_password(self, password):
        """设置密码哈希"""
        self.password_hash = pwd_context.hash(password)

    def verify_password(self, password):
        """验证密码"""
        return pwd_context.verify(password, self.password_hash)

    def __str__(self):
        return self.username