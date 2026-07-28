from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_core.common.security.worker_auth import WorkerAuthVerifier
from antcode_core.common.utils.worker_request import build_worker_signed_headers
from antcode_web_api.routes.v1 import workers_direct_control, workers_direct_spider
from antcode_web_api.routes.v1 import workers_direct_control_routes as direct_routes
from antcode_web_api.routes.v1.workers_direct_models import DirectSpiderItemsRequest, DirectSpiderMetaRequest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from redis.cluster import key_slot

HTTP_OK = 200
HTTP_FORBIDDEN = 403
HTTP_UNAUTHORIZED = 401
HTTP_PRECONDITION_FAILED = 412
HTTP_UNPROCESSABLE_ENTITY = 422


class _Redis:
    def __init__(self, error: Exception | None = None, item_result: list[int] | None = None) -> None:
        self.calls: list[tuple] = []
        self._error = error
        self._item_result = item_result or [1, 1, 0]

    async def evalsha(self, _sha: str, numkeys: int, *args):
        from antcode_core.spider_item_writer import _WRITE_ITEMS_LUA

        self.calls.append((_WRITE_ITEMS_LUA, numkeys, *args))
        if self._error:
            raise self._error
        return self._item_result

    async def eval(self, *args):
        self.calls.append(args)
        if self._error:
            raise self._error
        return 1


def _item(*, run_id: str = "run-1", project_id: str = "project-1") -> dict:
    return {
        "item_id": "item-1",
        "run_id": run_id,
        "project_id": project_id,
        "spider_name": "rule",
        "item_type": "default",
        "data": "{}",
        "url": "https://example.com",
        "timestamp": "2026-07-27T00:00:00Z",
        "sequence": "1",
    }


def _items_request(**changes) -> DirectSpiderItemsRequest:
    values = {
        "operation": "spider-items",
        "lease_id": "lease-1",
        "run_id": "run-1",
        "project_id": "project-1",
        "items": [_item()],
    }
    values.update(changes)
    return DirectSpiderItemsRequest(**values)


def _install(monkeypatch, redis: _Redis) -> AsyncMock:
    owns = AsyncMock()
    monkeypatch.setattr(workers_direct_spider, "require_worker_owns_spider_run", owns)
    monkeypatch.setattr(workers_direct_control, "_redis_client", AsyncMock(return_value=redis))
    return owns


@pytest.mark.asyncio
async def test_items_use_one_slot_atomic_lease_ownership_and_storage_fence(monkeypatch) -> None:
    redis = _Redis()
    owns = _install(monkeypatch, redis)

    response = await workers_direct_spider.ingest_direct_spider_items(
        SimpleNamespace(public_id="worker-1"),
        _items_request(),
    )

    assert response.data == {"written": True, "accepted": 1, "inserted": 1, "duplicates": 0}
    owns.assert_awaited_once()
    script, numkeys, *rest = redis.calls[0]
    keys = rest[:numkeys]
    assert len({key_slot(key.encode()) for key in keys}) == 1
    assert "redis.call('TIME')" in script
    assert "redis.call('GET', owner_key)" in script
    assert "redis.call('XADD', stream" not in script
    assert "'XADD', stream" in script


@pytest.mark.asyncio
async def test_items_report_idempotent_replay_counts(monkeypatch) -> None:
    redis = _Redis(item_result=[1, 0, 1])
    _install(monkeypatch, redis)

    response = await workers_direct_spider.ingest_direct_spider_items(
        SimpleNamespace(public_id="worker-1"),
        _items_request(),
    )

    assert response.data == {"written": True, "accepted": 1, "inserted": 0, "duplicates": 1}


@pytest.mark.asyncio
async def test_stale_lease_is_precondition_failure_without_success_ack(monkeypatch) -> None:
    _install(monkeypatch, _Redis(RuntimeError("SPIDER_LEASE_STALE")))

    with pytest.raises(HTTPException) as exc_info:
        await workers_direct_spider.ingest_direct_spider_items(
            SimpleNamespace(public_id="worker-1"),
            _items_request(),
        )

    assert exc_info.value.status_code == HTTP_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_stale_lease_from_database_precheck_is_precondition_failure(monkeypatch) -> None:
    from antcode_core.application.services.workers.spider_run_access import StaleSpiderLeaseError

    owns = AsyncMock(side_effect=StaleSpiderLeaseError("SpiderData lease_id 与 TaskRun 代际不匹配"))
    monkeypatch.setattr(workers_direct_spider, "require_worker_owns_spider_run", owns)

    with pytest.raises(HTTPException) as exc_info:
        await workers_direct_spider.ingest_direct_spider_items(
            SimpleNamespace(public_id="worker-1"),
            _items_request(),
        )

    assert exc_info.value.status_code == HTTP_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_item_cannot_redirect_write_to_another_run(monkeypatch) -> None:
    redis = _Redis()
    _install(monkeypatch, redis)
    request = _items_request(items=[_item(run_id="run-foreign")])

    with pytest.raises(HTTPException) as exc_info:
        await workers_direct_spider.ingest_direct_spider_items(SimpleNamespace(public_id="worker-1"), request)

    assert exc_info.value.status_code == HTTP_FORBIDDEN
    assert redis.calls == []


@pytest.mark.asyncio
async def test_item_rejects_invalid_data_json_before_redis_write(monkeypatch) -> None:
    redis = _Redis()
    _install(monkeypatch, redis)
    item = _item()
    item["data"] = "{broken"

    with pytest.raises(HTTPException) as exc_info:
        await workers_direct_spider.ingest_direct_spider_items(
            SimpleNamespace(public_id="worker-1"),
            _items_request(items=[item]),
        )

    assert exc_info.value.status_code == HTTP_UNPROCESSABLE_ENTITY
    assert redis.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["NaN", "Infinity", "-Infinity", "[" * 100_000 + "]" * 100_000])
async def test_item_rejects_non_strict_or_too_deep_json(monkeypatch, data: str) -> None:
    redis = _Redis()
    _install(monkeypatch, redis)
    item = _item()
    item["data"] = data

    with pytest.raises(HTTPException) as exc_info:
        await workers_direct_spider.ingest_direct_spider_items(
            SimpleNamespace(public_id="worker-1"),
            _items_request(items=[item]),
        )

    assert exc_info.value.status_code == HTTP_UNPROCESSABLE_ENTITY
    assert redis.calls == []


def test_item_id_is_canonicalized_before_write() -> None:
    item = _item()
    item["item_id"] = "  item-1  "

    normalized = workers_direct_spider._normalize_item(item, _items_request(items=[item]))

    assert normalized["item_id"] == "item-1"


@pytest.mark.asyncio
async def test_meta_cannot_redirect_index_to_another_project(monkeypatch) -> None:
    redis = _Redis()
    _install(monkeypatch, redis)
    request = DirectSpiderMetaRequest(
        operation="spider-meta",
        lease_id="lease-1",
        run_id="run-1",
        project_id="project-1",
        meta={"run_id": "run-1", "project_id": "project-foreign", "status": "running"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await workers_direct_spider.ingest_direct_spider_meta(SimpleNamespace(public_id="worker-1"), request)

    assert exc_info.value.status_code == HTTP_FORBIDDEN
    assert redis.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"nested": True}, ["nested"], None, True, float("nan")])
async def test_meta_rejects_non_redis_scalar_values(monkeypatch, value) -> None:
    redis = _Redis()
    _install(monkeypatch, redis)
    request = DirectSpiderMetaRequest(
        operation="spider-meta",
        lease_id="lease-1",
        run_id="run-1",
        project_id="project-1",
        meta={"status": value},
    )

    with pytest.raises(HTTPException) as exc_info:
        await workers_direct_spider.ingest_direct_spider_meta(SimpleNamespace(public_id="worker-1"), request)

    assert exc_info.value.status_code == HTTP_UNPROCESSABLE_ENTITY
    assert redis.calls == []


def test_spider_route_enforces_hmac_api_key_and_path_worker_binding(monkeypatch) -> None:
    worker = SimpleNamespace(public_id="worker-1", transport_mode="direct")

    async def load_secret(worker_id: str) -> str | None:
        return "secret" if worker_id == worker.public_id else None

    async def claim_nonce(_worker_id: str, _nonce: str) -> bool:
        return True

    class AllowAll:
        async def is_allowed(self, _identifier: str, _limit: int, _period: int) -> bool:
            return True

    verifier = WorkerAuthVerifier(load_secret, claim_nonce, AllowAll())

    async def verify(request: Request):
        auth = await verifier.verify_request(request)
        if request.headers.get("Authorization") != "Bearer api-key":
            raise HTTPException(status_code=HTTP_UNAUTHORIZED, detail="无效的 API Key")
        return {"worker": worker, "auth_info": auth}

    ingest = AsyncMock(return_value={"success": True, "data": {"accepted": 1}})
    monkeypatch.setattr(direct_routes, "ingest_direct_spider_items", ingest)
    router = APIRouter(prefix="/workers")
    direct_routes.register_direct_control_routes(router, verify)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    payload = _items_request().model_dump()
    headers = build_worker_signed_headers(worker, api_key="api-key", secret_key="secret", payload=payload)

    valid = client.post("/workers/worker-1/direct-control/spider-items", json=payload, headers=headers)
    mismatch = client.post("/workers/worker-2/direct-control/spider-items", json=payload, headers=headers)
    invalid_headers = {**headers, "X-Signature": "invalid"}
    invalid = client.post(
        "/workers/worker-1/direct-control/spider-items",
        json=payload,
        headers=invalid_headers,
    )

    assert valid.status_code == HTTP_OK
    assert mismatch.status_code == HTTP_FORBIDDEN
    assert invalid.status_code == HTTP_UNAUTHORIZED
