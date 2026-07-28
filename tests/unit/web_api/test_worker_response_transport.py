from datetime import UTC, datetime
from types import SimpleNamespace

from antcode_web_api.routes.v1.workers import _worker_to_response


def _worker(**overrides):
    values = {
        "public_id": "worker-1",
        "name": "Worker 1",
        "host": "127.0.0.1",
        "port": 8001,
        "status": "online",
        "region": "",
        "description": "",
        "tags": [],
        "version": "",
        "metrics": None,
        "capabilities": None,
        "last_heartbeat": None,
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_worker_response_does_not_default_missing_transport_mode() -> None:
    response = _worker_to_response(_worker())

    assert response.transportMode is None


def test_worker_response_preserves_empty_transport_mode() -> None:
    response = _worker_to_response(_worker(transport_mode=""))

    assert response.transportMode == ""
