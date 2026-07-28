"""
Worker 安装 Key 模型

一次性安装 Key，用于 Worker 快速注册。
"""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from tortoise import fields

from antcode_core.common.security.network_source import normalize_ip_or_cidr
from antcode_core.domain.models.base import BaseModel

INSTALL_KEY_TTL_HOURS = 24

if TYPE_CHECKING:
    from tortoise.backends.base.client import BaseDBAsyncClient


def _hash_install_key(plaintext: str) -> str:
    """P2-11：只存 install key 的 SHA-256（hex）到 DB，明文只在生成时返回给用户一次。

    hex（64 字符）与旧字段 `key` 的 max_length=64 兼容；同一唯一索引直接
    转成 hash 索引，不需要迁移 schema，只需一次数据回填。
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class WorkerInstallKey(BaseModel):
    """Worker 安装 Key 模型

    用于生成一次性安装命令，Worker 使用此 Key 进行注册。
    类似 nezha 探针的工作模式。
    """

    # 唯一安装 Key 哈希（SHA-256 hex）
    key = fields.CharField(max_length=64, unique=True, description="安装Key")

    # 状态: pending(待使用), used(已使用), expired(已过期)
    status = fields.CharField(max_length=20, default="pending", description="状态")

    # 操作系统类型: linux, macos, windows
    os_type = fields.CharField(max_length=20, description="操作系统类型")

    # 创建者用户 ID
    created_by = fields.BigIntField(description="创建者用户ID")

    # 注册来源限制；PostgreSQL 是权威存储，Redis meta 仅作缓存。
    allowed_source = fields.CharField(max_length=64, null=True, description="允许的注册 IP/CIDR")

    # 使用此 Key 注册的 Worker public_id
    used_by_worker = fields.CharField(max_length=32, null=True, description="使用此Key注册的Worker")

    # 使用时间
    used_at = fields.DatetimeField(null=True, description="使用时间")

    # V2 可恢复注册：只保存高熵恢复秘密的哈希，不保存可逆 API Key。
    registration_id = fields.CharField(max_length=32, null=True, unique=True)
    recovery_secret_hash = fields.CharField(max_length=64, null=True)
    registration_request_hash = fields.CharField(max_length=64, null=True)
    credential_derivation_version = fields.SmallIntField(null=True)
    recovery_expires_at = fields.DatetimeField(null=True)
    registration_acknowledged_at = fields.DatetimeField(null=True)

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
        *,
        allowed_source: str | None = None,
    ) -> "WorkerInstallKey":
        """创建新的安装 Key

        P2-11：DB 只存 SHA-256(明文) 到 ``key`` 列；明文以 ``plaintext_key``
        属性挂在返回实例上（不持久化，仅供 API 首次返回给用户）。

        Args:
            os_type: 操作系统类型 (linux/macos/windows)
            created_by: 创建者用户 ID
            allowed_source: 可选注册来源 IP/CIDR

        Returns:
            WorkerInstallKey 实例（含内存属性 ``plaintext_key``）
        """
        plaintext = cls.generate_key()
        expires_at = datetime.now(UTC) + timedelta(hours=INSTALL_KEY_TTL_HOURS)
        normalized_source = normalize_ip_or_cidr(allowed_source)

        instance = await cls.create(
            key=cls.hash_plaintext(plaintext),
            os_type=os_type.lower(),
            created_by=created_by,
            allowed_source=normalized_source,
            expires_at=expires_at,
            status="pending",
        )
        # 只在返回值上挂明文，不写 DB
        instance.plaintext_key = plaintext  # type: ignore[attr-defined]
        return instance

    @classmethod
    async def find_by_plaintext(cls, plaintext: str) -> "WorkerInstallKey | None":
        """P2-11：按明文 install key 查找记录（内部对明文取 SHA-256）。"""
        if not plaintext:
            return None
        return await cls.get_or_none(key=cls.hash_plaintext(plaintext))

    @staticmethod
    def hash_plaintext(plaintext: str) -> str:
        """返回持久化和事务查询使用的安装 Key 哈希。"""
        return _hash_install_key(plaintext)

    @classmethod
    def matches_plaintext(cls, stored_hash: str, plaintext: str) -> bool:
        """恒定时间对比：DB 里的 hash 是否等于对明文再取 hash。"""
        return hmac.compare_digest(
            (stored_hash or "").encode("utf-8"),
            cls.hash_plaintext(plaintext or "").encode("utf-8"),
        )

    def is_valid(self) -> bool:
        """检查 Key 是否有效。

        ``expires_at`` 在某些 DB 后端(如旧 SQLite 字段)取回时可能是
        naive datetime,如果直接和 ``datetime.now(UTC)`` 比较会触发
        ``TypeError``,绕过这条校验。这里统一把 naive 视作 UTC
        再比较,确保时区一致(P2-#L11)。
        """
        if self.status != "pending":
            return False
        now = datetime.now(UTC)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return now < expires_at

    @classmethod
    async def cas_claim_pending(
        cls,
        plaintext_key: str,
        worker_public_id: str,
        *,
        allowed_source: str,
        using_db: "BaseDBAsyncClient | None" = None,
    ) -> bool:
        """P1-10 + P2-11：按 SHA-256(明文) 原子占用一次性 Key。

        DB 层 CAS：只有 status='pending' 且未过期才能被 UPDATE 成 'used'；
        并发同来源的两条请求最多一条能占用成功，另一条 update rows 为 0。
        调用方必须把本方法、Worker 创建和 ``finalize_claim`` 放在同一事务。

        Returns:
            True: 本调用成功占用了 Key（唯一赢家）。
            False: Key 已被其他并发请求消费，本调用应放弃。
        """
        now = datetime.now(UTC)
        normalized_source = normalize_ip_or_cidr(allowed_source)
        if normalized_source is None:
            raise ValueError("消费安装 Key 时必须提供注册来源 IP/CIDR")
        updated = (
            await cls.filter(
                key=cls.hash_plaintext(plaintext_key),
                status="pending",
                expires_at__gt=now,
            )
            .using_db(using_db)
            .update(
                status="used",
                used_by_worker=worker_public_id,
                used_at=now,
                allowed_source=normalized_source,
            )
        )
        return bool(updated)

    @classmethod
    async def finalize_claim(
        cls,
        plaintext_key: str,
        placeholder_public_id: str,
        worker_public_id: str,
        *,
        using_db: "BaseDBAsyncClient",
    ) -> int:
        """把事务内占位 Worker ID 原子替换成真实 public_id。"""
        return await (
            cls.filter(
                key=cls.hash_plaintext(plaintext_key),
                status="used",
                used_by_worker=placeholder_public_id,
            )
            .using_db(using_db)
            .update(used_by_worker=worker_public_id)
        )

    class Meta:
        table = "worker_install_keys"
        indexes = [
            ("key",),
            ("status",),
            ("created_by",),
        ]


__all__ = ["WorkerInstallKey"]
