from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import pytest_asyncio
from antcode_core.application.services.workers.worker_heartbeat_service import (
    WorkerHeartbeatService,
)
from antcode_core.domain.models import Worker, WorkerStatus
from antcode_core.domain.models.worker_install_key import WorkerInstallKey
from tortoise import Tortoise

INITIAL_ACL_REVISION = 1
UPDATED_ACL_REVISION = 2
INITIAL_MEMORY_MB = 256
UPDATED_MEMORY_MB = 512


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path):
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'worker-heartbeat.sqlite3'}",
        modules={
            "models": [
                "antcode_core.domain.models.worker",
                "antcode_core.domain.models.worker_install_key",
            ]
        },
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()
    await Tortoise._reset_apps()


@pytest.mark.asyncio
async def test_heartbeat_does_not_overwrite_concurrent_admin_or_credential_fields():
    worker = await Worker.create(
        name="worker-1",
        host="127.0.0.1",
        api_key_hash="old-key",
        redis_acl_revision=INITIAL_ACL_REVISION,
        resource_limits={"memory_mb": INITIAL_MEMORY_MB},
        status=WorkerStatus.ONLINE.value,
    )
    stale = await Worker.get(id=worker.id)
    await Worker.filter(id=worker.id).update(
        api_key_hash="rotated-key",
        redis_acl_revision=UPDATED_ACL_REVISION,
        resource_limits={"memory_mb": UPDATED_MEMORY_MB},
        status=WorkerStatus.MAINTENANCE.value,
    )

    service = WorkerHeartbeatService()
    service._sync_cache_on_heartbeat = Mock()
    assert await service.heartbeat(
        stale,
        status_value=WorkerStatus.ONLINE.value,
        metrics={"cpu": 0.0},
        version="1.2.3",
    )

    persisted = await Worker.get(id=worker.id)
    assert persisted.api_key_hash == "rotated-key"
    assert persisted.redis_acl_revision == UPDATED_ACL_REVISION
    assert persisted.resource_limits == {"memory_mb": UPDATED_MEMORY_MB}
    assert persisted.status == WorkerStatus.MAINTENANCE.value
    assert persisted.metrics == {"cpu": 0.0}
    assert persisted.version == "1.2.3"
    assert persisted.last_heartbeat is not None


@pytest.mark.asyncio
async def test_offline_cas_does_not_override_a_newer_heartbeat():
    worker = await Worker.create(name="worker-2", host="127.0.0.1", status=WorkerStatus.ONLINE.value)
    stale = await Worker.get(id=worker.id)
    await Worker.filter(id=worker.id).update(last_heartbeat=datetime.now(UTC))

    updated = await WorkerHeartbeatService._mark_worker_status(
        stale,
        WorkerStatus.OFFLINE.value,
        protect_heartbeat=True,
    )

    assert updated is False
    assert (await Worker.get(id=worker.id)).status == WorkerStatus.ONLINE.value


@pytest.mark.asyncio
async def test_incremental_cache_discovers_worker_before_full_refresh_ttl():
    service = WorkerHeartbeatService()
    await service.init_heartbeat_cache()
    worker = await Worker.create(name="worker-new", host="127.0.0.1")

    await service.refresh_worker_cache()

    assert service._worker_cache[worker.id].name == "worker-new"
    assert service._worker_states[worker.id]["next_check"] is not None


@pytest.mark.asyncio
async def test_v2_registration_heartbeat_stays_connecting_until_acknowledged():
    worker = await Worker.create(
        name="worker-awaiting-ack",
        host="127.0.0.1",
        status=WorkerStatus.CONNECTING.value,
    )
    install_key = await WorkerInstallKey.create(
        key="a" * 64,
        status="used",
        os_type="linux",
        created_by=1,
        used_by_worker=worker.public_id,
        registration_id="b" * 32,
        expires_at=datetime.now(UTC),
    )
    service = WorkerHeartbeatService()

    assert await service.heartbeat(worker, status_value=WorkerStatus.ONLINE.value)
    persisted = await Worker.get(id=worker.id)
    assert persisted.status == WorkerStatus.CONNECTING.value
    assert persisted.last_heartbeat is not None

    await WorkerInstallKey.filter(id=install_key.id).update(registration_acknowledged_at=datetime.now(UTC))
    assert await service.heartbeat(persisted, status_value=WorkerStatus.ONLINE.value)
    assert (await Worker.get(id=worker.id)).status == WorkerStatus.ONLINE.value


@pytest.mark.asyncio
async def test_health_check_cannot_promote_unacknowledged_v2_worker():
    worker = await Worker.create(
        name="worker-health-awaiting-ack",
        host="127.0.0.1",
        status=WorkerStatus.CONNECTING.value,
    )
    await WorkerInstallKey.create(
        key="c" * 64,
        status="used",
        os_type="linux",
        created_by=1,
        used_by_worker=worker.public_id,
        registration_id="d" * 32,
        expires_at=datetime.now(UTC),
    )

    updated = await WorkerHeartbeatService._mark_worker_status(worker, WorkerStatus.ONLINE.value)

    assert updated is False
    assert (await Worker.get(id=worker.id)).status == WorkerStatus.CONNECTING.value
