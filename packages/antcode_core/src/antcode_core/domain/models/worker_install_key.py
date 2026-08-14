"""
Worker 安装 Key 模型

一次性安装 Key，用于 Worker 快速注册。
"""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from tortoise import fields

from antcode_core.common.security.network_source import normalize_ip_or_cidr
from antcode_core.domain.models.base import BaseModel

INSTALL_KEY_TTL_HOURS = 24
INSTALL_KEY_STATUS_PENDING = "pending"
INSTALL_KEY_STATUS_USED = "used"
INSTALL_KEY_STATUS_EXPIRED = "expired"
INSTALL_KEY_STATUS_REVOKED = "revoked"


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

    # 状态: pending(待使用), used(已使用), expired(已过期), revoked(已撤销)
    status = fields.CharField(max_length=20, default=INSTALL_KEY_STATUS_PENDING, description="状态")

    # 操作系统类型: linux, macos, windows
    os_type = fields.CharField(max_length=20, description="操作系统类型")

    # 创建者用户 ID
    created_by = fields.BigIntField(description="创建者用户ID")

    # 注册来源限制；PostgreSQL 是唯一权威存储。
    allowed_source = fields.CharField(max_length=64, null=True, description="允许的注册 IP/CIDR")

    # 使用此 Key 注册的 Worker public_id
    used_by_worker = fields.CharField(max_length=32, null=True, description="使用此Key注册的Worker")

    # 使用时间
    used_at = fields.DatetimeField(null=True, description="使用时间")

    # V2 可恢复注册：只保存高熵恢复秘密的哈希，不保存可逆 API Key。
    registration_id = fields.CharField(max_length=32, null=True)
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
    async def persist_install_key(
        cls,
        plaintext: str,
        os_type: str,
        created_by: int,
        *,
        allowed_source: str | None = None,
    ) -> "WorkerInstallKey":
        """持久化调用方已经生成、且仅能返回一次的安装 Key 明文。"""
        if not plaintext:
            raise ValueError("安装 Key 明文不能为空")
        expires_at = datetime.now(UTC) + timedelta(hours=INSTALL_KEY_TTL_HOURS)
        normalized_source = normalize_ip_or_cidr(allowed_source)

        instance = await cls.create(
            key=cls.hash_plaintext(plaintext),
            os_type=os_type.lower(),
            created_by=created_by,
            allowed_source=normalized_source,
            expires_at=expires_at,
            status=INSTALL_KEY_STATUS_PENDING,
        )
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
        if self.status != INSTALL_KEY_STATUS_PENDING:
            return False
        now = datetime.now(UTC)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return now < expires_at

    @classmethod
    async def expire_pending(cls, now: datetime, *, limit: int) -> int:
        """分批把已经超过有效期的未使用 Key 收敛为 expired。"""
        expired_ids = await (
            cls.filter(status=INSTALL_KEY_STATUS_PENDING, expires_at__lte=now)
            .order_by("id")
            .limit(limit)
            .values_list("id", flat=True)
        )
        if not expired_ids:
            return 0
        return await cls.filter(
            id__in=list(expired_ids),
            status=INSTALL_KEY_STATUS_PENDING,
            expires_at__lte=now,
        ).update(status=INSTALL_KEY_STATUS_EXPIRED)

    @classmethod
    async def revoke_pending(cls, public_id: str) -> str | None:
        """撤销一个未使用 Key；返回当前状态，记录不存在时返回 None。"""
        now = datetime.now(UTC)
        await cls.filter(
            public_id=public_id,
            status=INSTALL_KEY_STATUS_PENDING,
            expires_at__lte=now,
        ).update(status=INSTALL_KEY_STATUS_EXPIRED)
        updated = await cls.filter(
            public_id=public_id,
            status=INSTALL_KEY_STATUS_PENDING,
            expires_at__gt=now,
        ).update(status=INSTALL_KEY_STATUS_REVOKED)
        if updated == 1:
            return INSTALL_KEY_STATUS_REVOKED
        record = await cls.filter(public_id=public_id).only("status").first()
        return str(record.status) if record is not None else None

    class Meta:
        table = "worker_install_keys"
        indexes = [
            ("key",),
            ("status",),
            ("created_by",),
        ]


__all__ = [
    "INSTALL_KEY_STATUS_EXPIRED",
    "INSTALL_KEY_STATUS_PENDING",
    "INSTALL_KEY_STATUS_REVOKED",
    "INSTALL_KEY_STATUS_USED",
    "WorkerInstallKey",
]
