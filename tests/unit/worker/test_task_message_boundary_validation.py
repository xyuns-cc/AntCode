"""TaskMessage validation at Direct and Gateway transport boundaries."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.common.security.task_payload_envelope import (
    TaskPayloadEnvelopeError,
    seal_ready_payload,
)
from antcode_worker.transport.gateway.codecs import CodecError, TaskDecoder
from antcode_worker.transport.redis.transport import RedisTransport
from antcode_worker.transport.task_message_validation import validate_task_environment

MAX_TASK_TIMEOUT_SECONDS = 86400
NESTED_ANSWER = 42
NORMALIZED_PRIORITY = 2
VALID_TIMEOUT_SECONDS = 30
WORKER_SECRET = "worker-secret-with-at-least-thirty-two-bytes"


def test_task_environment_preserves_explicit_credentials_verbatim() -> None:
    environment = {"BUSINESS_API_KEY": "secret-value", "MY_SETTING": "enabled"}

    assert validate_task_environment(environment) == environment


@pytest.mark.parametrize(
    "name",
    ["PATH", "PYTHONPATH", "LD_PRELOAD", "BASH_ENV", "ANTCODE_SPIDER_EGRESS_PROXY"],
)
def test_task_environment_rejects_worker_owned_or_process_hijack_names(name: str) -> None:
    with pytest.raises(ValueError, match="保留"):
        validate_task_environment({name: "attacker-value"})


@pytest.mark.parametrize("environment", [{"NAME": 1}, {"BAD=NAME": "value"}, {"NAME": "bad\x00value"}])
def test_task_environment_rejects_non_process_safe_entries(environment: dict) -> None:
    with pytest.raises(ValueError, match="environment"):
        validate_task_environment(environment)


def _gateway_dispatch(**updates):
    values: dict = {
        "task_id": "task-1",
        "project_id": "project-1",
        "run_id": "run-1",
        "project_type": "rule",
        "priority": 0,
        "params": {},
        "environment": {},
        "timeout_seconds": VALID_TIMEOUT_SECONDS,
        "source_subdir": "",
        "entry_point": "",
        "runtime_env_name": "",
        "receipt_id": "receipt-1",
        "source_bundle_uri": "",
        "source_bundle_sha256": "",
    }
    values.update(updates)
    ready_payload = {
        key: value
        for key, value in values.items()
        if key not in {"receipt_id", "timeout_seconds", "params", "environment"}
    }
    ready_payload["timeout"] = values["timeout_seconds"]
    ready_payload["params"] = values["params"]
    ready_payload["environment"] = values["environment"]
    sealed = seal_ready_payload(
        ready_payload,
        worker_id="worker-1",
        worker_secret=WORKER_SECRET,
    )
    wire_values = {**values, "params": {}, "environment": {}}
    return SimpleNamespace(
        **wire_values,
        sealed_ready_payload=json.dumps(sealed, separators=(",", ":"), sort_keys=True).encode(),
    )


def _direct_frame(**updates) -> dict:
    digest = "a" * 64
    values = {
        "task_id": "task-1",
        "project_id": "project-1",
        "run_id": "run-1",
        "project_type": "code",
        "priority": "0",
        "params": {},
        "environment": {},
        "timeout": str(VALID_TIMEOUT_SECONDS),
        "dispatch_lease_id": "lease-1",
        "dispatch_lease_gen": "1",
        "source_bundle_uri": f"pgartifact://{digest}",
        "source_bundle_sha256": digest,
    }
    values.update(updates)
    return seal_ready_payload(
        values,
        worker_id="worker-1",
        worker_secret=WORKER_SECRET,
    )


def test_gateway_valid_envelope_is_decrypted_only_by_worker() -> None:
    dispatch = _gateway_dispatch(
        priority=NORMALIZED_PRIORITY,
        params={"kwargs": {"answer": NESTED_ANSWER}},
        environment={"MODE": "test"},
    )

    message = TaskDecoder.decode(dispatch, worker_id="worker-1", worker_secret=WORKER_SECRET)

    assert message.params == {"kwargs": {"answer": NESTED_ANSWER}}
    assert message.environment == {"MODE": "test"}
    assert message.timeout == VALID_TIMEOUT_SECONDS


def test_gateway_rejects_plaintext_proto_maps() -> None:
    dispatch = _gateway_dispatch()
    dispatch.params = {"token": "plaintext-forbidden"}

    with pytest.raises(CodecError, match="禁止携带明文"):
        TaskDecoder.decode(dispatch, worker_id="worker-1", worker_secret=WORKER_SECRET)


def test_gateway_rejects_wrong_worker_payload_secret() -> None:
    dispatch = _gateway_dispatch(params={"token": "encrypted-secret"})

    with pytest.raises(CodecError, match="authentication failed") as error:
        TaskDecoder.decode(
            dispatch,
            worker_id="worker-1",
            worker_secret="different-worker-secret-with-thirty-two-bytes",
        )
    assert "encrypted-secret" not in str(error.value)


def test_gateway_rejects_task_id_mirror_mismatch() -> None:
    dispatch = _gateway_dispatch()
    dispatch.task_id = "task-substituted"

    with pytest.raises(CodecError, match="task_id 与密文绑定不一致"):
        TaskDecoder.decode(dispatch, worker_id="worker-1", worker_secret=WORKER_SECRET)


@pytest.mark.asyncio
async def test_direct_valid_encrypted_frame_is_normalized() -> None:
    transport = RedisTransport(redis_url="redis://localhost/0", worker_id="worker-1")
    transport._lease_id = "lease-1"
    transport._task_payload_secret = WORKER_SECRET
    transport._require_current_generation = AsyncMock()

    message = await transport._build_guarded_task(
        "{antcode}:task:ready:worker-1",
        "1-0",
        _direct_frame(priority=str(NORMALIZED_PRIORITY)),
    )

    assert message is not None
    assert message.priority == NORMALIZED_PRIORITY
    assert message.params == {}
    assert message.environment == {}
    assert message.timeout == VALID_TIMEOUT_SECONDS


@pytest.mark.parametrize("field", ["task_id", "project_id", "run_id"])
def test_gateway_rejects_empty_task_identity(field: str) -> None:
    with pytest.raises(CodecError, match=field):
        TaskDecoder.decode(
            _gateway_dispatch(**{field: "  "}),
            worker_id="worker-1",
            worker_secret=WORKER_SECRET,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("priority", True),
        ("priority", 1.5),
        ("timeout_seconds", True),
        ("timeout_seconds", 0),
        ("timeout_seconds", MAX_TASK_TIMEOUT_SECONDS + 1),
    ],
)
def test_gateway_rejects_malformed_task_fields(field: str, value) -> None:
    with pytest.raises(CodecError):
        TaskDecoder.decode(
            _gateway_dispatch(**{field: value}),
            worker_id="worker-1",
            worker_secret=WORKER_SECRET,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("priority", "true"),
        ("priority", "1.5"),
        ("timeout", "true"),
        ("timeout", "0"),
        ("timeout", str(MAX_TASK_TIMEOUT_SECONDS + 1)),
    ],
)
async def test_direct_malformed_task_frame_is_dead_lettered(field: str, value) -> None:
    transport = RedisTransport(redis_url="redis://localhost/0", worker_id="worker-1")
    transport._lease_id = "lease-1"
    transport._task_payload_secret = WORKER_SECRET
    transport._require_current_generation = AsyncMock()
    transport._reclaimer = MagicMock(dead_letter_owned=AsyncMock())

    message = await transport._build_guarded_task(
        "{antcode}:task:ready:worker-1",
        "1-0",
        _direct_frame(**{field: value}),
    )

    assert message is None
    transport._reclaimer.dead_letter_owned.assert_awaited_once()
    payload = transport._reclaimer.dead_letter_owned.await_args.args[2]
    assert field.split("_", maxsplit=1)[0] in payload["_bad_frame_error"]


def test_direct_sensitive_payload_producer_rejects_non_mapping_params() -> None:
    with pytest.raises(TaskPayloadEnvelopeError, match="params must be an object"):
        _direct_frame(params=[])


@pytest.mark.asyncio
async def test_direct_frame_without_durable_run_id_is_dead_lettered() -> None:
    transport = RedisTransport(redis_url="redis://localhost/0", worker_id="worker-1")
    transport._lease_id = "lease-1"
    transport._task_payload_secret = WORKER_SECRET
    transport._require_current_generation = AsyncMock()
    transport._reclaimer = MagicMock(dead_letter_owned=AsyncMock())

    message = await transport._build_guarded_task(
        "{antcode}:task:ready:worker-1",
        "1-0",
        _direct_frame(run_id="", execution_id="legacy-run"),
    )

    assert message is None
    payload = transport._reclaimer.dead_letter_owned.await_args.args[2]
    assert "run_id" in payload["_bad_frame_error"]


def test_gateway_source_bundle_validation_remains_fail_closed() -> None:
    dispatch = _gateway_dispatch(
        project_type="code",
        source_bundle_uri="https://example.test/source.zip",
        source_bundle_sha256="a" * 64,
    )

    with pytest.raises(CodecError, match="pgartifact"):
        TaskDecoder.decode(dispatch, worker_id="worker-1", worker_secret=WORKER_SECRET)
