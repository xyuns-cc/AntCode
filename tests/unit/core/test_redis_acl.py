"""R10: Direct Worker 独立 Redis ACL 凭证管理。"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _ensure_encryption_key(monkeypatch):
    """secret_box 加解密需要 ENCRYPTION_KEY。"""
    monkeypatch.setenv("ENCRYPTION_KEY", "test-key-for-acl-suite-32-bytes-minimum")
    # 同时刷新 settings 缓存
    from antcode_core.common import config as cfg

    cfg.settings.ENCRYPTION_KEY = "test-key-for-acl-suite-32-bytes-minimum"
    cfg.settings.ENCRYPTION_KEY_SALT = "redis-acl-test-salt-value"
    cfg.settings.ENCRYPTION_LEGACY_KDF_SALT = ""
    cfg.settings.ENCRYPTION_ALLOW_LEGACY_SHA256 = False
    cfg.settings.REDIS_ACL_ENABLED = True


def _fake_worker(public_id="w-1", redis_username=None, redis_password_encrypted=None):
    return SimpleNamespace(
        public_id=public_id,
        redis_username=redis_username,
        redis_password_encrypted=redis_password_encrypted,
        redis_acl_revision=0,
        redis_acl_synced_at=None,
        save=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_ensure_worker_acl_sends_setuser_with_correct_username_and_keys():
    from antcode_core.common.security import redis_acl

    fake_redis = AsyncMock()
    worker = _fake_worker(public_id="w-1")

    plaintext = await redis_acl.ensure_worker_acl(fake_redis, worker)

    assert plaintext.startswith("rk_")
    assert worker.redis_username == "worker_w-1"
    assert worker.redis_acl_revision == 1
    assert worker.redis_acl_synced_at is not None
    # 验证加密后能解出原文
    from antcode_core.common.security.secret_box import secret_box

    assert secret_box.decrypt(worker.redis_password_encrypted) == plaintext

    # 第一条命令必为 ACL SETUSER worker_w-1 ...
    setuser_call = fake_redis.execute_command.await_args_list[0]
    args = setuser_call.args
    assert args[0] == "ACL"
    assert args[1] == "SETUSER"
    assert args[2] == "worker_w-1"
    flat = " ".join(args)
    assert "%RW~antcode:task:ready:w-1" in flat
    assert "(+xadd %W~antcode:task:result)" in args
    assert "log:ingest" not in flat
    assert "%RW~antcode:control:reply:w-1:*" in flat
    assert "%RW~{antcode}:control:settlement:w-1:*" in flat
    assert "spider" not in flat
    assert "%RW~{antcode}:lease:data:w-1" in flat
    assert "%R~{antcode}:lease:revoked:w-1" in flat
    assert "~{antcode}:lease:expiring" not in flat
    assert "~{antcode}:lease:active" not in flat
    assert "control:global" not in flat
    assert all("~" not in arg for arg in args if not arg.startswith("("))
    assert "reset" in args
    assert "+@read" not in flat
    assert "+@write" not in flat
    assert "+@stream" not in flat
    assert "+acl" not in flat
    assert "+config" not in flat
    # 第二条命令是 ACL SAVE
    save_call = fake_redis.execute_command.await_args_list[1]
    assert save_call.args[:2] == ("ACL", "SAVE")


@pytest.mark.asyncio
async def test_ensure_worker_acl_second_call_increments_revision_and_replaces_password():
    from antcode_core.common.security import redis_acl
    from antcode_core.common.security.secret_box import secret_box

    fake_redis = AsyncMock()
    worker = _fake_worker(public_id="w-2")

    first = await redis_acl.ensure_worker_acl(fake_redis, worker)
    assert worker.redis_acl_revision == 1

    second = await redis_acl.ensure_worker_acl(fake_redis, worker)
    assert first != second
    assert worker.redis_acl_revision == 2
    assert secret_box.decrypt(worker.redis_password_encrypted) == second


@pytest.mark.asyncio
async def test_ensure_worker_acl_database_failure_restores_previous_redis_password():
    from antcode_core.common.security import redis_acl
    from antcode_core.common.security.secret_box import secret_box

    old_encrypted = secret_box.encrypt("old-password")
    worker = _fake_worker(
        public_id="w-rollback",
        redis_username="worker_w-rollback",
        redis_password_encrypted=old_encrypted,
    )
    worker.redis_acl_revision = 4
    worker.redis_acl_synced_at = datetime.now(UTC)
    worker.save.side_effect = RuntimeError("database unavailable")
    redis = AsyncMock()

    with pytest.raises(RuntimeError, match="database unavailable"):
        await redis_acl.ensure_worker_acl(redis, worker, new_password="new-password")

    commands = [call.args for call in redis.execute_command.await_args_list]
    assert commands[0][:3] == ("ACL", "SETUSER", "worker_w-rollback")
    assert ">new-password" in commands[0]
    assert commands[2][:3] == ("ACL", "SETUSER", "worker_w-rollback")
    assert ">old-password" in commands[2]
    assert commands[1][:2] == commands[3][:2] == ("ACL", "SAVE")
    assert worker.redis_password_encrypted == old_encrypted
    assert worker.redis_acl_revision == 4


@pytest.mark.asyncio
async def test_ensure_worker_acl_ambiguous_setuser_failure_restores_snapshot():
    from antcode_core.common.security import redis_acl
    from antcode_core.common.security.secret_box import secret_box

    old_encrypted = secret_box.encrypt("old-password")
    worker = _fake_worker(
        public_id="w-ambiguous",
        redis_username="worker_w-ambiguous",
        redis_password_encrypted=old_encrypted,
    )
    redis = AsyncMock()
    redis.execute_command.side_effect = [RuntimeError("response lost"), None, None]

    with pytest.raises(RuntimeError, match="response lost"):
        await redis_acl.ensure_worker_acl(redis, worker, new_password="new-password")

    commands = [call.args for call in redis.execute_command.await_args_list]
    assert commands[0][:3] == ("ACL", "SETUSER", "worker_w-ambiguous")
    assert commands[1][:3] == ("ACL", "SETUSER", "worker_w-ambiguous")
    assert ">old-password" in commands[1]
    assert commands[2][:2] == ("ACL", "SAVE")
    assert worker.redis_password_encrypted == old_encrypted


@pytest.mark.asyncio
async def test_ensure_worker_acl_cancelled_setuser_restores_then_propagates():
    from antcode_core.common.security import redis_acl
    from antcode_core.common.security.secret_box import secret_box

    worker = _fake_worker(
        public_id="w-cancelled",
        redis_username="worker_w-cancelled",
        redis_password_encrypted=secret_box.encrypt("old-password"),
    )
    redis = AsyncMock()
    redis.execute_command.side_effect = [asyncio.CancelledError(), None, None]

    with pytest.raises(asyncio.CancelledError):
        await redis_acl.ensure_worker_acl(redis, worker, new_password="new-password")

    assert redis.execute_command.await_count == 3


@pytest.mark.asyncio
async def test_revoke_worker_acl_calls_deluser_and_clears_fields():
    from antcode_core.common.security import redis_acl
    from antcode_core.common.security.secret_box import secret_box

    fake_redis = AsyncMock()
    worker = _fake_worker(
        public_id="w-3",
        redis_username="worker_w-3",
        redis_password_encrypted=secret_box.encrypt("rk_existing"),
    )

    await redis_acl.revoke_worker_acl(fake_redis, worker)

    deluser_call = fake_redis.execute_command.await_args_list[0]
    assert deluser_call.args == ("ACL", "DELUSER", "worker_w-3")
    assert worker.redis_username is None
    assert worker.redis_password_encrypted is None


@pytest.mark.asyncio
async def test_revoke_worker_acl_ambiguous_deluser_failure_restores_snapshot():
    from antcode_core.common.security import redis_acl
    from antcode_core.common.security.secret_box import secret_box

    encrypted = secret_box.encrypt("old-password")
    worker = _fake_worker(
        public_id="w-revoke-ambiguous",
        redis_username="worker_w-revoke-ambiguous",
        redis_password_encrypted=encrypted,
    )
    redis = AsyncMock()
    redis.execute_command.side_effect = [RuntimeError("response lost"), None, None]

    with pytest.raises(RuntimeError, match="response lost"):
        await redis_acl.revoke_worker_acl(redis, worker)

    commands = [call.args for call in redis.execute_command.await_args_list]
    assert commands[0] == ("ACL", "DELUSER", "worker_w-revoke-ambiguous")
    assert commands[1][:3] == ("ACL", "SETUSER", "worker_w-revoke-ambiguous")
    assert ">old-password" in commands[1]
    assert worker.redis_username == "worker_w-revoke-ambiguous"
    assert worker.redis_password_encrypted == encrypted


@pytest.mark.asyncio
async def test_revoke_worker_acl_noop_if_no_username():
    from antcode_core.common.security import redis_acl

    fake_redis = AsyncMock()
    worker = _fake_worker(public_id="w-4")

    await redis_acl.revoke_worker_acl(fake_redis, worker)

    fake_redis.execute_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_worker_acl_disabled_raises(monkeypatch):
    from antcode_core.common import config as cfg
    from antcode_core.common.security import redis_acl

    monkeypatch.setattr(cfg.settings, "REDIS_ACL_ENABLED", False, raising=False)

    fake_redis = AsyncMock()
    worker = _fake_worker(public_id="w-5")
    with pytest.raises(RuntimeError, match="REDIS_ACL_ENABLED=false"):
        await redis_acl.ensure_worker_acl(fake_redis, worker)
    fake_redis.execute_command.assert_not_awaited()


def test_secrets_manager_store_persists_to_file_with_strict_mode(tmp_path):
    from antcode_worker.security.secrets import SecretsManager

    mgr = SecretsManager(secrets_dir=tmp_path)
    path = mgr.store("redis_username", "worker_w-1")
    assert path is not None and path.exists()
    assert path.read_text(encoding="utf-8") == "worker_w-1"

    # 0o600 权限
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600

    # 后续 get 直接命中缓存
    assert mgr.get("redis_username") == "worker_w-1"


def test_secrets_manager_failed_replace_preserves_existing_value(tmp_path, monkeypatch):
    import antcode_worker.security.secrets as secrets_module
    from antcode_worker.security.secrets import SecretsManager

    path = tmp_path / "redis_password"
    path.write_text("old-value", encoding="utf-8")
    manager = SecretsManager(secrets_dir=tmp_path)

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(secrets_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        manager.store("redis_password", "new-value")

    assert path.read_text(encoding="utf-8") == "old-value"


def test_secrets_manager_rejects_path_traversal(tmp_path):
    from antcode_worker.security.secrets import SecretsManager

    with pytest.raises(ValueError, match="非法凭据键名"):
        SecretsManager(secrets_dir=tmp_path).store("../outside", "secret")


def test_secrets_manager_known_keys_include_redis_username():
    from antcode_worker.security.secrets import SecretsManager

    assert "redis_username" in SecretsManager.KNOWN_KEYS
    assert "redis_password" in SecretsManager.KNOWN_KEYS
