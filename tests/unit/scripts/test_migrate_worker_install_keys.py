from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.migrate_worker_install_keys import (
    RedisKeyMigrationConflict,
    _is_hashed_install_key,
    _migrate_redis_keys,
    _migrate_rows,
    _migrated_redis_key,
)

_SCAN_COUNT = 500
_SHORT_TTL_MS = 5_000
_NORMAL_TTL_MS = 10_000
_LONG_TTL_MS = 12_000
_LEGACY_MIGRATION_COUNT, _TWO_KEYS, _PERSISTENT_PTTL_MS = 3, 2, -1


class _Connection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        for row in self.rows:
            row.setdefault("allowed_source", None)

    async def execute_query_dict(self, _sql, params):
        assert params == ["pending"]
        return [dict(row) for row in self.rows if row["status"] == "pending"]

    async def execute_query(self, _sql, params):
        if params == ["expired", "pending"]:
            expired = 0
            for row in self.rows:
                if row["status"] == "pending" and row.get("expired", False):
                    row["status"] = "expired"
                    expired += 1
            return expired, []
        hashed, allowed_source, row_id, plaintext, status = params
        for row in self.rows:
            if row["id"] == row_id and row["key"] == plaintext and row["status"] == status:
                row["key"] = hashed
                row["allowed_source"] = allowed_source
                return 1, []
        return 0, []


class _Redis:
    def __init__(self, keys: set[str] | None = None, values: dict[str, str] | None = None) -> None:
        self.data: dict[str, SimpleNamespace] = {}
        for key in keys or set():
            payload = (values or {}).get(key, f"value:{key}").encode()
            self.data[key] = SimpleNamespace(payload=payload, pttl_ms=30_000, redis_type="string")
        self.restored: list[tuple[str, int, bytes, bool]] = []
        self.deleted: list[str] = []
        self.pexpired: list[tuple[str, int]] = []

    @property
    def keys(self) -> set[str]:
        return set(self.data)

    def add(self, key: str, payload: bytes, *, pttl_ms: int = 30_000, redis_type: str = "string") -> None:
        self.data[key] = SimpleNamespace(payload=payload, pttl_ms=pttl_ms, redis_type=redis_type)

    async def get(self, key: str):
        value = self.data.get(key)
        return value.payload if value is not None else None

    async def scan_iter(self, *, match, count):
        assert match == "antcode:worker:install-key:*"
        assert count == _SCAN_COUNT
        for key in list(self.data):
            yield key

    async def type(self, key: str):
        value = self.data.get(key)
        return (value.redis_type if value is not None else "none").encode()

    async def dump(self, key: str):
        value = self.data.get(key)
        return value.payload if value is not None else None

    async def pttl(self, key: str):
        value = self.data.get(key)
        return value.pttl_ms if value is not None else -2

    async def restore(self, key: str, ttl_ms: int, payload: bytes, *, replace: bool):
        assert key not in self.data
        self.restored.append((key, ttl_ms, payload, replace))
        self.data[key] = SimpleNamespace(payload=payload, pttl_ms=-1 if ttl_ms == 0 else ttl_ms, redis_type="string")

    async def delete(self, key: str):
        self.deleted.append(key)
        return int(self.data.pop(key, None) is not None)

    async def pexpire(self, key: str, ttl_ms: int):
        self.pexpired.append((key, ttl_ms))
        self.data[key].pttl_ms = ttl_ms
        return True

    async def persist(self, key: str):
        self.data[key].pttl_ms = -1
        return True


class _DeleteInterruptedRedis(_Redis):
    def __init__(self) -> None:
        super().__init__()
        self.interrupt_once = True

    async def delete(self, key: str):
        if self.interrupt_once:
            self.interrupt_once = False
            raise ConnectionError("connection lost after RESTORE")
        return await super().delete(key)


class _SourceExpiresBeforeDeleteRedis(_Redis):
    def __init__(self, source: str) -> None:
        super().__init__()
        self.source = source

    async def delete(self, key: str):
        if key == self.source:
            self.data.pop(key, None)
            return 0
        return await super().delete(key)


@pytest.mark.asyncio
async def test_pending_plaintext_keys_are_migrated_idempotently() -> None:
    existing_hash = "a" * 64
    connection = _Connection(
        [
            {"id": 1, "key": "OLD-PLAINTEXT-KEY", "status": "pending"},
            {"id": 2, "key": existing_hash, "status": "pending"},
            {"id": 3, "key": "USED-PLAINTEXT-KEY", "status": "used"},
        ]
    )
    redis = _Redis(
        {
            "antcode:worker:install-key:meta:OLD-PLAINTEXT-KEY",
            f"antcode:worker:install-key:meta:{existing_hash}",
        },
        {
            "antcode:worker:install-key:meta:OLD-PLAINTEXT-KEY": '{"allowed_source":"10.2.3.4/24"}',
            f"antcode:worker:install-key:meta:{existing_hash}": '{"allowed_source":""}',
        },
    )

    assert await _migrate_rows(connection, redis, "antcode") == 1
    assert _is_hashed_install_key(connection.rows[0]["key"])
    assert connection.rows[0]["allowed_source"] == "10.2.3.0/24"
    assert connection.rows[1]["key"] == existing_hash
    assert connection.rows[1]["allowed_source"] is None
    assert connection.rows[2]["key"] == "USED-PLAINTEXT-KEY"
    assert await _migrate_rows(connection, redis, "antcode") == 0


@pytest.mark.asyncio
async def test_pending_key_without_redis_metadata_fails_closed() -> None:
    connection = _Connection([{"id": 1, "key": "OLD-KEY", "status": "pending"}])

    with pytest.raises(RuntimeError, match="缺少 Redis 来源元数据"):
        await _migrate_rows(connection, _Redis(set()), "antcode")


@pytest.mark.asyncio
async def test_expired_pending_key_does_not_require_expired_redis_metadata() -> None:
    connection = _Connection([{"id": 1, "key": "EXPIRED-KEY", "status": "pending", "expired": True}])

    assert await _migrate_rows(connection, _Redis(set()), "antcode") == 1
    assert connection.rows[0]["status"] == "expired"


@pytest.mark.asyncio
async def test_pending_key_with_hostname_source_is_rejected() -> None:
    metadata_key = "antcode:worker:install-key:meta:OLD-KEY"
    connection = _Connection([{"id": 1, "key": "OLD-KEY", "status": "pending"}])
    redis = _Redis({metadata_key}, {metadata_key: '{"allowed_source":"worker.example.com"}'})

    with pytest.raises(RuntimeError, match="来源元数据无效"):
        await _migrate_rows(connection, redis, "antcode")


@pytest.mark.asyncio
async def test_legacy_redis_keys_are_renamed_without_plaintext_components() -> None:
    token = "A" * 32
    redis = _Redis(
        {
            f"antcode:worker:install-key:meta:{token}",
            f"antcode:worker:install-key:fail:{token}:2001:db8::1",
            f"antcode:worker:install-key:nonce:{token}:nonce:with:colon",
            "antcode:worker:install-key:meta:" + "a" * 64,
        }
    )

    assert await _migrate_redis_keys(redis, "antcode") == _LEGACY_MIGRATION_COUNT
    assert all(token not in key for key in redis.keys)
    assert all("2001:db8::1" not in key for key in redis.keys)
    assert all("nonce:with:colon" not in key for key in redis.keys)


@pytest.mark.asyncio
async def test_redis_migration_uses_dump_restore_and_preserves_remaining_ttl() -> None:
    token = "A" * 32
    source = f"antcode:worker:install-key:meta:{token}"
    redis = _Redis()
    persistent_source = f"antcode:worker:install-key:claim:{token}"
    redis.add(source, b"metadata", pttl_ms=_LONG_TTL_MS)
    redis.add(persistent_source, b"10.0.0.1", pttl_ms=_PERSISTENT_PTTL_MS)

    assert await _migrate_redis_keys(redis, "antcode") == _TWO_KEYS

    assert source not in redis.keys
    target, ttl_ms, _payload, replace = next(entry for entry in redis.restored if entry[2] == b"metadata")
    assert target != source
    assert 0 < ttl_ms <= _LONG_TTL_MS
    assert replace is False
    assert any(entry[1] == 0 and entry[2] == b"10.0.0.1" for entry in redis.restored)


@pytest.mark.asyncio
async def test_retry_converges_matching_restore_residue_and_caps_target_ttl() -> None:
    token = "A" * 32
    source = f"antcode:worker:install-key:meta:{token}"
    redis = _Redis()
    redis.add(source, b"same", pttl_ms=_SHORT_TTL_MS)

    target = _migrated_redis_key(source)
    assert target is not None
    redis.add(target, b"same", pttl_ms=20_000)

    assert await _migrate_redis_keys(redis, "antcode") == 1
    assert source not in redis.keys
    assert redis.restored == []
    assert redis.pexpired and redis.pexpired[0][0] == target
    assert 0 < redis.pexpired[0][1] <= _SHORT_TTL_MS
    assert await _migrate_redis_keys(redis, "antcode") == 0


@pytest.mark.asyncio
async def test_retry_recovers_interruption_between_restore_and_delete() -> None:
    token = "A" * 32
    source = f"antcode:worker:install-key:meta:{token}"
    redis = _DeleteInterruptedRedis()
    redis.add(source, b"metadata", pttl_ms=_NORMAL_TTL_MS)

    with pytest.raises(ConnectionError, match="after RESTORE"):
        await _migrate_redis_keys(redis, "antcode")
    assert len(redis.keys) == _TWO_KEYS

    assert await _migrate_redis_keys(redis, "antcode") == 1
    assert len(redis.keys) == 1
    assert source not in redis.keys


@pytest.mark.asyncio
async def test_source_expiry_after_restore_removes_target_instead_of_reviving_key() -> None:
    token = "A" * 32
    source = f"antcode:worker:install-key:nonce:{token}:request-nonce"
    redis = _SourceExpiresBeforeDeleteRedis(source)
    redis.add(source, b"1", pttl_ms=1_000)

    assert await _migrate_redis_keys(redis, "antcode") == 0
    assert redis.keys == set()


@pytest.mark.asyncio
async def test_existing_target_with_different_payload_fails_without_deleting_either_key() -> None:
    token = "A" * 32
    source = f"antcode:worker:install-key:meta:{token}"
    redis = _Redis()
    redis.add(source, b"source")

    target = _migrated_redis_key(source)
    assert target is not None
    redis.add(target, b"different")

    with pytest.raises(RedisKeyMigrationConflict, match="内容冲突"):
        await _migrate_redis_keys(redis, "antcode")
    assert {source, target}.issubset(redis.keys)
    assert redis.deleted == []


def test_install_key_migration_moves_redis_keys_before_opening_the_db_transaction() -> None:
    script = Path("scripts/migrate_worker_install_keys.py").read_text(encoding="utf-8")
    migration = Path("migrations/models/20260713_add_worker_install_key_allowed_source.sql").read_text(encoding="utf-8")

    assert 'ADD COLUMN IF NOT EXISTS "allowed_source" VARCHAR(64)' in migration
    assert 'async with in_transaction("default")' in script
    # Redis 搬迁必须先于 DB 事务：反过来会让 DB 事务在 Redis 搬迁失败时已经提交，
    # 留下一批列已加、但来源元数据仍在旧 key 下的 pending 安装 Key。
    assert script.index("redis_migrated = await _migrate_redis_keys(redis_client, namespace)") < script.index(
        'async with in_transaction("default")'
    )
