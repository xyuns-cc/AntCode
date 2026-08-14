from __future__ import annotations

import json

import pytest
from antcode_core.common.config import settings
from antcode_core.common.security.secret_box import secret_box
from antcode_core.common.security.task_payload_diagnostics import redact_persisted_task_frame
from antcode_core.common.security.task_payload_envelope import (
    ENVELOPE_FIELD,
    TaskPayloadDecryptionError,
    TaskPayloadEnvelopeError,
    open_ready_payload,
    open_redispatch_payload,
    seal_ready_payload,
    seal_redispatch_payload,
)

WORKER_SECRET = "worker-task-payload-secret-material-0001"
WRONG_WORKER_SECRET = "worker-task-payload-secret-material-0002"


@pytest.fixture(autouse=True)
def _control_encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "task-payload-control-key-material-000001")
    monkeypatch.setattr(settings, "ENCRYPTION_KEY_SALT", "task-payload-test-salt")
    monkeypatch.setattr(settings, "ENCRYPTION_KEYS_LEGACY", "")
    monkeypatch.setattr(settings, "ENCRYPTION_LEGACY_KDF_SALT", "")
    monkeypatch.setattr(settings, "ENCRYPTION_ALLOW_LEGACY_SHA256", False)
    secret_box._cached = None
    secret_box._cache_key = None
    yield
    secret_box._cached = None
    secret_box._cache_key = None


def test_ready_envelope_round_trip_hides_sensitive_values() -> None:
    payload = {
        "task_id": "run-1",
        "params": {"token": "ready-secret-sentinel"},
        "environment": {"DB_PASSWORD": "environment-secret-sentinel"},
    }

    sealed = seal_ready_payload(payload, worker_id="worker-1", worker_secret=WORKER_SECRET)
    persisted = json.dumps(sealed)

    assert "ready-secret-sentinel" not in persisted
    assert "environment-secret-sentinel" not in persisted
    assert "params" not in sealed
    assert "environment" not in sealed
    assert open_ready_payload(sealed, worker_id="worker-1", worker_secret=WORKER_SECRET) == payload


def test_ready_envelope_rejects_tampering_and_wrong_worker_key() -> None:
    sealed = seal_ready_payload(
        {"params": {"token": "secret"}, "environment": {}},
        worker_id="worker-1",
        worker_secret=WORKER_SECRET,
    )
    envelope = json.loads(sealed[ENVELOPE_FIELD])
    envelope["ciphertext"] = envelope["ciphertext"][:-2] + "AA"
    tampered = {**sealed, ENVELOPE_FIELD: json.dumps(envelope)}

    with pytest.raises(TaskPayloadDecryptionError, match="authentication failed"):
        open_ready_payload(tampered, worker_id="worker-1", worker_secret=WORKER_SECRET)
    with pytest.raises(TaskPayloadDecryptionError, match="authentication failed"):
        open_ready_payload(sealed, worker_id="worker-1", worker_secret=WRONG_WORKER_SECRET)


def test_ready_envelope_rejects_plaintext_and_unknown_versions() -> None:
    with pytest.raises(TaskPayloadEnvelopeError, match="plaintext"):
        open_ready_payload(
            {"params": {"token": "forbidden"}},
            worker_id="worker-1",
            worker_secret=WORKER_SECRET,
        )
    sealed = seal_ready_payload(
        {"params": {}, "environment": {}},
        worker_id="worker-1",
        worker_secret=WORKER_SECRET,
    )
    envelope = json.loads(sealed[ENVELOPE_FIELD])
    envelope["version"] = 1
    sealed[ENVELOPE_FIELD] = json.dumps(envelope)

    with pytest.raises(TaskPayloadEnvelopeError, match="version"):
        open_ready_payload(sealed, worker_id="worker-1", worker_secret=WORKER_SECRET)


def test_ready_envelope_authenticates_every_public_field() -> None:
    sealed = seal_ready_payload(
        {
            "task_id": "task-1",
            "run_id": "run-1",
            "project_id": "project-1",
            "project_type": "code",
            "priority": 3,
            "timeout": 120,
            "runtime_env_name": "py312",
            "source_bundle_uri": "s3://bundle",
            "source_bundle_sha256": "a" * 64,
            "source_bundle_size": 42,
            "entry_point": "main.py",
            "dispatch_lease_id": "lease-1",
            "dispatch_lease_gen": 7,
            "trace_parent": "00-trace-span-01",
            "custom_public_metadata": {"nested": True},
            "params": {"token": "secret"},
            "environment": {},
        },
        worker_id="worker-1",
        worker_secret=WORKER_SECRET,
    )
    for field in set(sealed).difference({ENVELOPE_FIELD}):
        tampered = {**sealed, field: f"{sealed[field]}-tampered"}
        with pytest.raises(TaskPayloadDecryptionError, match="binding mismatch"):
            open_ready_payload(tampered, worker_id="worker-1", worker_secret=WORKER_SECRET)


def test_ready_envelope_strips_transport_settlement_metadata() -> None:
    sealed = seal_ready_payload(
        {"task_id": "task-1", "params": {"token": "secret"}, "environment": {}},
        worker_id="worker-1",
        worker_secret=WORKER_SECRET,
    )
    requeued = {
        **sealed,
        "requeue_count": "1",
        "requeue_reason": "invalid task dispatch",
        "requeue_at": "2026-08-11T00:00:00Z",
    }

    restored = open_ready_payload(requeued, worker_id="worker-1", worker_secret=WORKER_SECRET)

    assert restored == {"task_id": "task-1", "params": {"token": "secret"}, "environment": {}}


def test_ready_seal_rejects_redispatch_environment_alias() -> None:
    with pytest.raises(TaskPayloadEnvelopeError, match="unexpected sensitive fields"):
        seal_ready_payload(
            {"params": {}, "environment": {}, "environment_vars": {"TOKEN": "secret"}},
            worker_id="worker-1",
            worker_secret=WORKER_SECRET,
        )


@pytest.mark.parametrize(("field", "value"), [("params", []), ("environment", None)])
def test_ready_seal_rejects_invalid_sensitive_objects(field: str, value) -> None:
    with pytest.raises(TaskPayloadEnvelopeError, match=field):
        seal_ready_payload(
            {"params": {}, "environment": {}, field: value},
            worker_id="worker-1",
            worker_secret=WORKER_SECRET,
        )


def test_redispatch_envelope_round_trip_hides_sensitive_values() -> None:
    payload = {
        "run_id": "run-1",
        "params": {"token": "redispatch-secret-sentinel"},
        "environment_vars": {"API_KEY": "redispatch-env-sentinel"},
    }

    sealed = seal_redispatch_payload(payload)
    persisted = json.dumps(sealed)

    assert "redispatch-secret-sentinel" not in persisted
    assert "redispatch-env-sentinel" not in persisted
    assert open_redispatch_payload(sealed) == payload


def test_redispatch_envelope_rejects_wrong_control_key(monkeypatch) -> None:
    sealed = seal_redispatch_payload({"params": {"token": "secret"}, "environment_vars": {}})
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "different-control-key-material-000000001")
    secret_box._cached = None
    secret_box._cache_key = None

    with pytest.raises(TaskPayloadDecryptionError, match="authentication failed"):
        open_redispatch_payload(sealed)


def test_redispatch_envelope_authenticates_every_public_field() -> None:
    payload = {
        "run_id": "run-1",
        "task_id": 1,
        "project_id": "project-1",
        "runtime_env_name": "py312",
        "timeout": 120,
        "project_type": "spider",
        "region": None,
        "require_render": False,
        "attempts": 2,
        "reason": "retry",
        "enqueued_at_ms": 1234,
        "params": {"token": "secret"},
        "environment_vars": {},
    }
    sealed = seal_redispatch_payload(payload)

    for field in set(sealed).difference({ENVELOPE_FIELD}):
        tampered = {**sealed, field: _different_value(sealed[field])}
        with pytest.raises(TaskPayloadDecryptionError, match="binding mismatch"):
            open_redispatch_payload(tampered)


def test_redispatch_envelope_rejects_version_one() -> None:
    sealed = seal_redispatch_payload({"params": {}, "environment_vars": {}})
    envelope = json.loads(sealed[ENVELOPE_FIELD])
    envelope["version"] = 1
    sealed[ENVELOPE_FIELD] = json.dumps(envelope)

    with pytest.raises(TaskPayloadEnvelopeError, match="version"):
        open_redispatch_payload(sealed)


def _different_value(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    return "tampered" if value is None else f"{value}-tampered"


def test_poison_frame_redaction_drops_envelope_and_unknown_fields() -> None:
    redacted = redact_persisted_task_frame(
        {
            "task_id": "task-1",
            ENVELOPE_FIELD: '{"unexpected":"envelope-secret"}',
            "params": '{"token":"plaintext-secret"}',
            "custom_secret": "unknown-secret",
        }
    )

    assert redacted["task_id"] == "task-1"
    assert set(redacted) == {"task_id", "payload_sha256"}
    assert "secret" not in str(redacted)
