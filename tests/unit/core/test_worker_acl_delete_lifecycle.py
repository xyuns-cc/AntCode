from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from antcode_core.application.services.workers.worker_service import (
    WorkerService,
    _revoke_worker_redis_access,
)
from antcode_core.common import config as config_module
from antcode_core.common.security.redis_acl import revoke_worker_acl, sync_all_worker_acls
from antcode_core.domain.models import Worker


@pytest.mark.asyncio
async def test_revoke_failure_preserves_worker_credentials() -> None:
    redis = AsyncMock()
    redis.execute_command.side_effect = [1, RuntimeError("ACL SAVE failed"), "OK", "OK"]
    worker = SimpleNamespace(
        public_id="worker-1",
        redis_username="worker_worker-1",
        redis_password_encrypted="encrypted",
        redis_acl_revision=1,
        redis_acl_synced_at="timestamp",
        save=AsyncMock(),
    )

    with patch(
        "antcode_core.common.security.redis_acl_recovery.secret_box.decrypt",
        return_value="old-password",
    ):
        with pytest.raises(RuntimeError, match="ACL SAVE failed"):
            await revoke_worker_acl(redis, worker)

    assert worker.redis_username == "worker_worker-1"
    assert worker.redis_password_encrypted == "encrypted"
    worker.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_delete_stops_before_database_when_acl_revoke_fails(monkeypatch) -> None:
    revoker = AsyncMock(side_effect=RuntimeError("Redis unavailable"))
    service = WorkerService(acl_revoker=revoker, lease_disabler=AsyncMock(return_value=True))
    worker = SimpleNamespace(
        public_id="worker-1",
        api_key_hash="api-hash",
        api_key_previous_hash=None,
        api_key_previous_expires_at=None,
        secret_key_hash="secret-hash",
        secret_key_encrypted="encrypted-secret",
        redis_username="worker_worker-1",
        redis_acl_synced_at="timestamp",
        save=AsyncMock(),
    )
    service.get_worker_by_id = AsyncMock(return_value=worker)
    service._cascade_delete_worker_data = AsyncMock()
    monkeypatch.setattr(config_module.settings, "REDIS_ACL_ENABLED", True)

    with (
        patch(
            "antcode_core.application.services.workers.worker_service.quiesce_worker_for_delete",
            AsyncMock(),
        ),
        pytest.raises(RuntimeError, match="Redis unavailable"),
    ):
        await service.delete_worker("worker-1")

    revoker.assert_awaited_once_with(worker)
    assert worker.redis_acl_synced_at is None
    assert worker.save.await_args_list[-1] == call(update_fields=["redis_acl_synced_at"])
    service._cascade_delete_worker_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_redis_revoker_clears_persisted_credentials() -> None:
    redis = AsyncMock()
    worker = SimpleNamespace()
    revoke = AsyncMock()

    with (
        patch("antcode_core.infrastructure.redis.get_redis_client", AsyncMock(return_value=redis)),
        patch("antcode_core.common.security.redis_acl.revoke_worker_acl", revoke),
    ):
        await _revoke_worker_redis_access(worker)

    revoke.assert_awaited_once_with(redis, worker, clear_credentials=True)


@pytest.mark.asyncio
async def test_database_delete_failure_does_not_restore_revoked_credentials(monkeypatch) -> None:
    async def clear_credentials(worker):
        worker.redis_username = None
        worker.redis_password_encrypted = None
        await worker.save(update_fields=["redis_username", "redis_password_encrypted"])

    service = WorkerService(acl_revoker=clear_credentials, lease_disabler=AsyncMock(return_value=True))
    worker = SimpleNamespace(
        public_id="worker-1",
        api_key_hash="api-hash",
        api_key_previous_hash=None,
        api_key_previous_expires_at=None,
        secret_key_hash="secret-hash",
        secret_key_encrypted="encrypted-secret",
        redis_username="worker_worker-1",
        redis_password_encrypted="encrypted",
        redis_acl_synced_at="timestamp",
        save=AsyncMock(),
    )
    service.get_worker_by_id = AsyncMock(return_value=worker)
    service._cascade_delete_worker_data = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr(config_module.settings, "REDIS_ACL_ENABLED", True)

    with (
        patch(
            "antcode_core.application.services.workers.worker_service.quiesce_worker_for_delete",
            AsyncMock(),
        ),
        pytest.raises(RuntimeError, match="database unavailable"),
    ):
        await service.delete_worker("worker-1")

    assert worker.redis_username is None
    assert worker.redis_password_encrypted is None
    assert worker.redis_acl_synced_at is None


class _AsyncWorkers:
    def __init__(self, workers):
        self._workers = workers

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for worker in self._workers:
            yield worker


@pytest.mark.asyncio
async def test_startup_sync_completes_pending_revocation(monkeypatch) -> None:
    redis = AsyncMock()
    worker = SimpleNamespace(
        public_id="worker-1",
        redis_username="worker_worker-1",
        redis_password_encrypted="encrypted",
        redis_acl_synced_at=None,
        save=AsyncMock(),
    )
    monkeypatch.setattr(config_module.settings, "REDIS_ACL_ENABLED", True)

    with (
        patch.object(Worker, "filter", return_value=_AsyncWorkers([worker])),
        patch(
            "antcode_core.common.security.redis_acl_recovery.secret_box.decrypt",
            return_value="old-password",
        ),
    ):
        result = await sync_all_worker_acls(redis)

    assert result == {"synced": 0, "revoked": 1, "failed": []}
    commands = [call.args for call in redis.execute_command.await_args_list]
    assert ("ACL", "DELUSER", "worker_worker-1") in commands
    assert not any(command[:2] == ("ACL", "SETUSER") for command in commands)
    assert worker.redis_username is None
    assert worker.redis_password_encrypted is None


@pytest.mark.asyncio
async def test_batch_delete_mixed_ids_reports_only_missing_workers(monkeypatch) -> None:
    credential_fields = {
        "api_key_hash": None,
        "api_key_previous_hash": None,
        "api_key_previous_expires_at": None,
        "secret_key_hash": None,
        "secret_key_encrypted": None,
    }
    first = SimpleNamespace(
        id=7,
        public_id="worker-7",
        name="first",
        redis_username=None,
        save=AsyncMock(),
        **credential_fields,
    )
    second = SimpleNamespace(
        id=8,
        public_id="worker-8",
        name="second",
        redis_username=None,
        save=AsyncMock(),
        **credential_fields,
    )
    id_query = MagicMock(all=AsyncMock(return_value=[first]))
    public_query = MagicMock(all=AsyncMock(return_value=[second]))
    service = WorkerService(lease_disabler=AsyncMock(return_value=True))
    service._cascade_delete_worker_data = AsyncMock(return_value={})
    monkeypatch.setattr(config_module.settings, "REDIS_ACL_ENABLED", True)

    with (
        patch.object(Worker, "filter", side_effect=[id_query, public_query]),
        patch(
            "antcode_core.application.services.workers.worker_service.quiesce_worker_for_delete",
            AsyncMock(),
        ),
    ):
        result = await service.batch_delete_workers([7, "worker-8", "missing"])

    assert result["success_count"] == 2
    assert result["failed_count"] == 1
    assert result["failed_ids"] == ["missing"]
