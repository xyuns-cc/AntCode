"""
Worker 安装 Key 模型

一次性安装 Key，用于 Worker 快速注册。
"""

from datetime import UTC, datetime, timedelta

from tortoise import fields

from antcode_core.domain.models.base import BaseModel


class WorkerInstallKey(BaseModel):
    """Worker 安装 Key 模型

    用于生成一次性安装命令，Worker 使用此 Key 进行注册。
    类似 nezha 探针的工作模式。
    """

    # 唯一安装 Key（32位随机字符串）
    key = fields.CharField(max_length=64, unique=True, description="安装Key")

    # 状态: pending(待使用), used(已使用), expired(已过期)
    status = fields.CharField(max_length=20, default="pending", description="状态")

    # 操作系统类型: linux, macos, windows
    os_type = fields.CharField(max_length=20, description="操作系统类型")

    # 创建者用户 ID
    created_by = fields.BigIntField(description="创建者用户ID")

    # 使用此 Key 注册的 Worker public_id
    used_by_worker = fields.CharField(
        max_length=32, null=True, description="使用此Key注册的Worker"
    )

    # 使用时间
    used_at = fields.DatetimeField(null=True, description="使用时间")

    # 过期时间（默认24小时后过期）
    expires_at = fields.DatetimeField(description="过期时间")

    # 时间戳
    created_at = fields.DatetimeField(auto_now_add=True)

    def __str__(self):
        return f"InstallKey({self.key[:8]}...)"

    @classmethod
    def generate_key(cls) -> str:
        """生成随机安装 Key"""
        import secrets

        return secrets.token_hex(16).upper()

    @classmethod
    async def create_install_key(
        cls,
        os_type: str,
        created_by: int,
        expires_hours: int = 24,
    ) -> "WorkerInstallKey":
        """创建新的安装 Key

        Args:
            os_type: 操作系统类型 (linux/macos/windows)
            created_by: 创建者用户 ID
            expires_hours: 过期时间（小时），默认24小时

        Returns:
            WorkerInstallKey 实例
        """
        key = cls.generate_key()
        expires_at = datetime.now(UTC) + timedelta(hours=expires_hours)

        return await cls.create(
            key=key,
            os_type=os_type.lower(),
            created_by=created_by,
            expires_at=expires_at,
            status="pending",
        )

    def is_valid(self) -> bool:
        """检查 Key 是否有效"""
        if self.status != "pending":
            return False
        if datetime.now(UTC) > self.expires_at:
            return False
        return True

    async def mark_used(self, worker_public_id: str) -> None:
        """标记 Key 为已使用

        Args:
            worker_public_id: 使用此 Key 注册的 Worker public_id
        """
        self.status = "used"
        self.used_by_worker = worker_public_id
        self.used_at = datetime.now(UTC)
        await self.save()

    class Meta:
        table = "worker_install_keys"
        indexes = [
            ("key",),
            ("status",),
            ("created_by",),
        ]


__all__ = ["WorkerInstallKey"]
