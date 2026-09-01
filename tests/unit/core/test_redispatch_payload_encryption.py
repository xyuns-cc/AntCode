from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from antcode_core.application.services.scheduler.redispatch_service import RedispatchService
from antcode_core.common.config import settings
from antcode_core.common.security.secret_box import secret_box
from antcode_core.common.security.task_payload_envelope import ENVELOPE_FIELD

redispatch_module = importlib.import_module("antcode_core.application.services.scheduler.redispatch_service")

PARAM_SENTINEL = "redispatch-param-secret-sentinel"
ENV_SENTINEL = "redispatch-environment-secret-sentinel"


class _Pipeline:
    def __init__(self, redis: _Redis) -> None:
        self._redis = redis
        self._commands: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def xadd(self, *args, **kwargs):
        self._commands.append(("xadd", args, kwargs))
        return self

    def zadd(self, *args, **kwargs):
        self._commands.append(("zadd", args, kwargs))
        return self

    def hdel(self, *args, **kwargs):
        self._commands.append(("hdel", args, kwargs))
        return self

    async def execute(self) -> list[Any]:
        results = []
        for name, args, kwargs in self._commands:
            operation = getattr(self._redis, name)
            results.append(await operation(*args, **kwargs))
        return results


class _Redis:
    def __init__(self) -> None:
        self.pending: dict[str, float] = {}
        self.processing: dict[str, str] = {}
        self.dead_letters: list[dict[str, str]] = []

    async def zadd(self, _key: str, mapping: dict[str, float]) -> int:
        self.pending.update(mapping)
        return len(mapping)

    async def script_load(self, _script: str) -> str:
        return "claim-sha"

    async def evalsha(self, *_args) -> list[str]:
        claimed = list(self.pending)
        self.pending.clear()
        self.processing.update(dict.fromkeys(claimed, "1"))
        return claimed

    async def hdel(self, _key: str, raw_payload: str) -> int:
        return int(self.processing.pop(raw_payload, None) is not None)

    async def hgetall(self, _key: str) -> dict[str, str]:
        return dict(self.processing)

    async def xadd(self, _key: str, payload: dict[str, str], **_kwargs) -> str:
        self.dead_letters.append(payload)
        return "1-0"

    def pipeline(self, *, transaction: bool) -> _Pipeline:
        assert transaction is True
        return _Pipeline(self)


@pytest.fixture(autouse=True)
def _encryption_settings(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "redispatch-control-key-material-00000001")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "redispatch-test-salt")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", "")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    secret_box._cached = None
    secret_box._cache_key = None
    yield
    secret_box._cached = None
    secret_box._cache_key = None


@pytest.fixture
def redispatch(monkeypatch) -> tuple[RedispatchService, _Redis]:
    redis = _Redis()

    async def get_redis() -> _Redis:
        return redis

    monkeypatch.setattr(redispatch_module, "get_redis_client", get_redis)
    return RedispatchService(), redis


async def _enqueue(service: RedispatchService) -> None:
    assert await service.enqueue(
        run_id="run-1",
        task_id=7,
        project_id="project-1",
        params={"token": PARAM_SENTINEL},
        environment_vars={"API_KEY": ENV_SENTINEL},
        timeout=90,
        region="cn-east",
        require_render=True,
    )


@pytest.mark.asyncio
async def test_pending_processing_and_claim_keep_sensitive_values_encrypted(redispatch) -> None:
    service, redis = redispatch
    await _enqueue(service)
    raw_payload = next(iter(redis.pending))

    assert PARAM_SENTINEL not in raw_payload
    assert ENV_SENTINEL not in raw_payload
    assert ENVELOPE_FIELD in json.loads(raw_payload)

    claimed = await service.claim_due()

    assert claimed[0]["params"] == {"token": PARAM_SENTINEL}
    assert claimed[0]["environment_vars"] == {"API_KEY": ENV_SENTINEL}
    assert claimed[0]["region"] == "cn-east"
    assert claimed[0]["require_render"] is True
    assert next(iter(redis.processing)) == raw_payload
    assert PARAM_SENTINEL not in next(iter(redis.processing))


@pytest.mark.asyncio
async def test_tampered_redispatch_ciphertext_moves_to_redacted_dlq(redispatch) -> None:
    service, redis = redispatch
    await _enqueue(service)
    raw_payload = next(iter(redis.pending))
    payload = json.loads(raw_payload)
    envelope = json.loads(payload[ENVELOPE_FIELD])
    envelope["ciphertext"] = envelope["ciphertext"][:-2] + "AA"
    payload[ENVELOPE_FIELD] = json.dumps(envelope)
    redis.pending = {json.dumps(payload): 1}

    assert await service.claim_due() == []
    assert redis.processing == {}
    assert len(redis.dead_letters) == 1
    assert PARAM_SENTINEL not in str(redis.dead_letters)
    assert set(redis.dead_letters[0]) == {
        "payload_sha256",
        "reason",
        "dead_letter_at_ms",
    }


@pytest.mark.asyncio
async def test_wrong_control_key_moves_redispatch_payload_to_dlq(redispatch, monkeypatch) -> None:
    service, redis = redispatch
    await _enqueue(service)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "different-redispatch-control-key-000001")
    secret_box._cached = None
    secret_box._cache_key = None

    assert await service.claim_due() == []
    assert redis.processing == {}
    assert redis.dead_letters[0]["reason"] == "TaskPayloadDecryptionError"


@pytest.mark.asyncio
async def test_legacy_plaintext_redispatch_payload_moves_to_redacted_dlq(redispatch) -> None:
    service, redis = redispatch
    legacy = json.dumps(
        {
            "run_id": "run-legacy",
            "project_id": "project-1",
            "params": {"token": PARAM_SENTINEL},
            "environment_vars": {"API_KEY": ENV_SENTINEL},
        }
    )
    redis.pending[legacy] = 1

    assert await service.claim_due() == []
    assert redis.processing == {}
    assert PARAM_SENTINEL not in str(redis.dead_letters)
    assert ENV_SENTINEL not in str(redis.dead_letters)
    assert redis.dead_letters[0]["reason"] == "TaskPayloadEnvelopeError"


@pytest.mark.asyncio
async def test_stalled_processing_requeues_identical_ciphertext(redispatch) -> None:
    service, redis = redispatch
    await _enqueue(service)
    raw_payload = next(iter(redis.pending))
    redis.pending.clear()
    redis.processing[raw_payload] = "0"

    assert await service.sweep_stalled() == 1
    assert list(redis.pending) == [raw_payload]
    assert redis.processing == {}
    assert PARAM_SENTINEL not in raw_payload
    assert ENV_SENTINEL not in raw_payload
