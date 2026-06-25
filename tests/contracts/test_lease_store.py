"""Contract tests for ``antcode_core.application.services.lease_service.LeaseStore``.

These run against the same Redis container used by the rest of
``tests/contracts/`` (see ``docker-compose.test.yml``).  They cover:

- first-time grant produces a fresh ``lease_id`` and marks the worker active
- renewal with the matching ``current_lease_id`` keeps the ``lease_id`` stable
  but pushes ``expires_at_ms`` forward
- mismatched ``current_lease_id`` (worker restart) issues a new ``lease_id``
- ``revoke`` removes the active marker
- ``sweep_expired`` removes leases past their TTL and reports the evicted ids

If Redis on ``localhost:16379`` is unreachable the entire module is skipped —
the existing transport conftest helper applies the same skip rule, so dev
machines without docker stay green.
"""

from __future__ import annotations

import asyncio

# NOTE: 直接走 importlib 而不是 ``from antcode_core.application.services
# .lease_service import ...``：现网 `antcode_core.application.services.__init__`
# 会主动 import 调度器模块（含一处与 P3 无关的 IndentationError），如果走
# package 导入会被它带崩。这里只关心 lease_service 单模块，绕开它。
import importlib.util as _ilu  # noqa: E402
import pathlib as _pl  # noqa: E402
import secrets

import pytest
import pytest_asyncio

from tests.contracts.conftest import REDIS_TEST_HOST, REDIS_TEST_PORT, REDIS_TEST_URL, _tcp_reachable

_lease_module_path = (
    _pl.Path(__file__).resolve().parents[2]
    / "packages"
    / "antcode_core"
    / "src"
    / "antcode_core"
    / "application"
    / "services"
    / "lease_service.py"
)
if not _lease_module_path.is_file():  # pragma: no cover - defensive
    pytest.skip(f"lease_service.py not found at {_lease_module_path}", allow_module_level=True)

import sys as _sys  # noqa: E402

_module_name = "antcode_lease_service_under_test"
_spec = _ilu.spec_from_file_location(_module_name, _lease_module_path)
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    pytest.skip("无法加载 lease_service 模块", allow_module_level=True)
_module = _ilu.module_from_spec(_spec)
# 必须先注册到 sys.modules，dataclass 装饰器需要通过 cls.__module__ 反查。
_sys.modules[_module_name] = _module
_spec.loader.exec_module(_module)

Lease = _module.Lease  # type: ignore[attr-defined]
LeasePolicy = _module.LeasePolicy  # type: ignore[attr-defined]
LeaseStore = _module.LeaseStore  # type: ignore[attr-defined]


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def redis_client():
    if not _tcp_reachable(REDIS_TEST_HOST, REDIS_TEST_PORT):
        pytest.skip(
            f"Redis on {REDIS_TEST_HOST}:{REDIS_TEST_PORT} unreachable; "
            "start it with `docker compose -f tests/contracts/docker-compose.test.yml up -d`"
        )
    try:
        import redis.asyncio as aioredis
    except ImportError:  # pragma: no cover
        pytest.skip("redis package not installed")

    client = aioredis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("redis not reachable for lease store tests")
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def lease_store(redis_client):
    namespace = f"antcode-test-{secrets.token_hex(4)}"
    policy = LeasePolicy(ttl_ms=2_000, renew_after_ms=500)
    store = LeaseStore(redis_client, namespace=namespace, policy=policy)
    try:
        yield store
    finally:
        # Scrub everything under this test's namespace.
        cursor = 0
        pattern = f"{namespace}:*"
        while True:
            cursor, keys = await redis_client.scan(cursor=cursor, match=pattern, count=500)
            if keys:
                await redis_client.delete(*keys)
            if cursor == 0:
                break


async def test_grant_first_time_marks_active(lease_store: LeaseStore, redis_client):
    worker_id = f"worker-{secrets.token_hex(3)}"

    lease = await lease_store.grant(worker_id, current_lease_id="")

    assert isinstance(lease, Lease)
    assert lease.worker_id == worker_id
    assert lease.lease_id  # non-empty
    assert lease.expires_at_ms > lease.granted_at_ms
    assert await lease_store.is_active(worker_id) is True

    # ZSet should also carry this worker at score == expires_at_ms.
    score = await redis_client.zscore(f"{lease_store.namespace}:lease:expiring", worker_id)
    assert score is not None
    assert int(score) == lease.expires_at_ms


async def test_grant_renew_with_matching_lease_id_keeps_id_and_pushes_expiry(
    lease_store: LeaseStore,
):
    worker_id = f"worker-{secrets.token_hex(3)}"

    first = await lease_store.grant(worker_id, current_lease_id="")
    # Sleep slightly so granted_at_ms is provably later.
    await asyncio.sleep(0.02)
    renewed = await lease_store.grant(worker_id, current_lease_id=first.lease_id)

    assert renewed.lease_id == first.lease_id, "续租应保留同一 lease_id"
    assert renewed.expires_at_ms >= first.expires_at_ms
    assert renewed.granted_at_ms >= first.granted_at_ms


async def test_grant_mismatched_lease_id_is_treated_as_new(lease_store: LeaseStore):
    worker_id = f"worker-{secrets.token_hex(3)}"

    first = await lease_store.grant(worker_id, current_lease_id="")
    reissued = await lease_store.grant(worker_id, current_lease_id="stale-lease-id")

    assert reissued.lease_id != first.lease_id, "current_lease_id 不匹配应重新发租"
    assert await lease_store.is_active(worker_id) is True


async def test_revoke_clears_active_set(lease_store: LeaseStore):
    worker_id = f"worker-{secrets.token_hex(3)}"

    await lease_store.grant(worker_id, current_lease_id="")
    assert await lease_store.is_active(worker_id) is True

    revoked = await lease_store.revoke(worker_id, reason="deregister")
    assert revoked is True
    assert await lease_store.is_active(worker_id) is False
    assert await lease_store.get(worker_id) is None

    # Revoking again is a no-op (returns False) but doesn't error.
    revoked_again = await lease_store.revoke(worker_id, reason="deregister")
    assert revoked_again is False


async def test_sweep_expired_evicts_past_due_leases(lease_store: LeaseStore):
    # Two workers grant at the same time; we'll fast-forward "now" past TTL.
    w1 = f"worker-1-{secrets.token_hex(3)}"
    w2 = f"worker-2-{secrets.token_hex(3)}"
    w_alive = f"worker-alive-{secrets.token_hex(3)}"

    lease1 = await lease_store.grant(w1, current_lease_id="")
    lease2 = await lease_store.grant(w2, current_lease_id="")
    lease_alive = await lease_store.grant(w_alive, current_lease_id="")

    # Simulate "now" 5s after expiry of the two doomed leases but well
    # before lease_alive's expiry — done by passing a now_ms in the
    # future for w1/w2 only via direct ZADD override.
    # Easiest: rewrite the score on the alive worker to far in the future
    # so it survives the sweep.
    far_future = lease_alive.expires_at_ms + 10 * lease_store.policy.ttl_ms
    await lease_store._redis.zadd(  # type: ignore[attr-defined]
        f"{lease_store.namespace}:lease:expiring",
        {w_alive: far_future},
    )

    # Use sweep with a now_ms past lease1.expires_at_ms.
    evicted = await lease_store.sweep_expired(
        now_ms=max(lease1.expires_at_ms, lease2.expires_at_ms) + 1,
    )

    assert set(evicted) == {w1, w2}
    assert await lease_store.is_active(w1) is False
    assert await lease_store.is_active(w2) is False
    assert await lease_store.is_active(w_alive) is True


async def test_grant_persists_metrics_json(lease_store: LeaseStore, redis_client):
    worker_id = f"worker-{secrets.token_hex(3)}"

    metrics = {"cpu": 12.5, "memory": 33.0, "running_tasks": 2}
    await lease_store.grant(worker_id, current_lease_id="", metrics=metrics)

    raw = await redis_client.hget(f"{lease_store.namespace}:lease:{worker_id}", "metrics_json")
    assert raw, "metrics_json 字段应被写入 Hash"
    # 用最朴素的字符串包含断言，避免依赖 JSON 顺序。
    assert "cpu" in raw and "running_tasks" in raw
