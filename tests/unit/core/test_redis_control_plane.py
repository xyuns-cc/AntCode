"""Redis 控制平面协议测试。"""

import pytest
from antcode_core.infrastructure.redis import (
    build_cancel_control_payload,
    build_config_update_control_payload,
    build_runtime_manage_control_payload,
    control_global_group,
    control_global_stream,
    control_group,
    control_reply_stream,
    control_stream,
    decode_stream_payload,
    direct_register_proof_key,
    install_key_redis_digest,
    log_chunk_stream_key,
    log_chunk_stream_pattern,
    log_stream_key,
    log_stream_pattern,
    require_runtime_control_request_id,
    runtime_control_request_id,
    runtime_control_settlement_key,
    task_ready_stream,
    task_result_stream,
    worker_group,
    worker_heartbeat_key,
    worker_install_key_block_key,
    worker_install_key_claim_key,
    worker_install_key_fail_counter_key,
    worker_install_key_meta_key,
    worker_install_key_nonce_key,
    worker_install_source_block_key,
    worker_install_source_fail_counter_key,
)


def test_control_plane_keys_use_default_namespace():
    assert task_ready_stream("worker-1") == "antcode:task:ready:worker-1"
    assert task_result_stream() == "antcode:task:result"
    assert control_stream("worker-1") == "antcode:control:worker-1"
    assert control_global_stream() == "antcode:control:global"
    assert control_global_group("worker-1") == "antcode-control:worker-1"
    assert control_reply_stream("req-1") == "antcode:control:reply:req-1"
    assert worker_heartbeat_key("worker-1") == "antcode:heartbeat:worker-1"
    assert worker_group() == "antcode-workers"
    assert control_group() == "antcode-control"


def test_runtime_control_keys_are_worker_scoped():
    nonce = "a" * 32
    request_id = runtime_control_request_id("worker-1", nonce)
    settlement_key = runtime_control_settlement_key("worker-1", "stream|1-0")

    assert request_id == f"worker-1:{nonce}"
    assert require_runtime_control_request_id(request_id, "worker-1") == request_id
    assert control_reply_stream(request_id) == f"antcode:control:reply:worker-1:{nonce}"
    assert settlement_key.startswith("{antcode}:control:settlement:worker-1:")
    assert len(settlement_key.rsplit(":", 1)[1]) == 64


@pytest.mark.parametrize("request_id", ["worker-2:" + "a" * 32, "worker-1:short", "worker-1:" + "A" * 32])
def test_runtime_control_request_id_rejects_foreign_or_malformed_values(request_id):
    with pytest.raises(ValueError):
        require_runtime_control_request_id(request_id, "worker-1")


def test_control_plane_log_keys_and_patterns():
    assert log_stream_key("run-1") == "antcode:log:stream:run-1"
    assert log_chunk_stream_key("run-1") == "antcode:log:chunk:run-1"
    assert log_stream_pattern() == "antcode:log:stream:*"
    assert log_chunk_stream_pattern("ac") == "ac:log:chunk:*"


def test_control_plane_worker_security_keys_use_namespace():
    assert direct_register_proof_key("worker-1") == "antcode:direct:register:worker-1"
    key_digest = install_key_redis_digest("K1")
    source_digest = install_key_redis_digest("10.0.0.1")
    nonce_value = "RAW-NONCE-VALUE"
    nonce_digest = install_key_redis_digest(nonce_value)
    assert worker_install_key_fail_counter_key("K1", "10.0.0.1") == (
        f"antcode:worker:install-key:fail:{key_digest}:{source_digest}"
    )
    assert worker_install_key_block_key("K1", "10.0.0.1") == (
        f"antcode:worker:install-key:block:{key_digest}:{source_digest}"
    )
    assert worker_install_key_claim_key("K1") == f"antcode:worker:install-key:claim:{key_digest}"
    assert worker_install_key_nonce_key("K1", nonce_value) == (
        f"antcode:worker:install-key:nonce:{key_digest}:{nonce_digest}"
    )
    assert worker_install_key_meta_key("K1") == f"antcode:worker:install-key:meta:{key_digest}"
    assert worker_install_source_fail_counter_key("10.0.0.1") == (
        f"antcode:worker:install-key:source-fail:{source_digest}"
    )
    assert worker_install_source_block_key("10.0.0.1") == (f"antcode:worker:install-key:source-block:{source_digest}")

    install_keys = (
        worker_install_key_fail_counter_key("K1", "10.0.0.1"),
        worker_install_key_block_key("K1", "10.0.0.1"),
        worker_install_key_claim_key("K1"),
        worker_install_key_nonce_key("K1", nonce_value),
        worker_install_key_meta_key("K1"),
    )
    assert all("K1" not in redis_key for redis_key in install_keys)
    assert all("10.0.0.1" not in redis_key for redis_key in install_keys)
    assert all(nonce_value not in redis_key for redis_key in install_keys)

    assert direct_register_proof_key("worker-1", namespace="ac") == "ac:direct:register:worker-1"


def test_cancel_payload_defaults_task_id_to_run_id():
    payload = build_cancel_control_payload(run_id="run-1", reason="manual")
    assert payload["control_type"] == "cancel"
    assert payload["task_id"] == "run-1"
    assert payload["run_id"] == "run-1"
    assert payload["reason"] == "manual"


def test_decode_stream_payload_parses_json_fields():
    raw = {
        b"config": b'{"a":1}',
        b"payload": b'{"x":"y"}',
        b"metrics": b'{"cpu":20.5}',
        b"run_id": b"run-1",
    }
    decoded = decode_stream_payload(raw)

    assert decoded["run_id"] == "run-1"
    assert decoded["config"] == {"a": 1}
    assert decoded["payload"] == {"x": "y"}
    assert decoded["metrics"] == {"cpu": 20.5}


def test_decode_stream_payload_rejects_malformed_json_fields():
    with pytest.raises(ValueError, match="config"):
        decode_stream_payload({b"config": b"{bad json"})


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_decode_stream_payload_rejects_nonstandard_json_constants(value):
    with pytest.raises(ValueError, match="data"):
        decode_stream_payload({"data": value})


def test_build_runtime_and_config_payloads():
    runtime_payload = build_runtime_manage_control_payload(
        action="list_envs",
        request_id="req-1",
        reply_stream=control_reply_stream("req-1"),
        payload={"scope": "project"},
        expires_at_ms=4_102_444_800_000,
    )
    assert runtime_payload["control_type"] == "runtime_manage"
    assert runtime_payload["action"] == "list_envs"
    assert runtime_payload["request_id"] == "req-1"
    assert runtime_payload["expires_at_ms"] == "4102444800000"

    config_payload = build_config_update_control_payload({"max_concurrent_tasks": 8})
    assert config_payload["control_type"] == "config_update"
    assert "max_concurrent_tasks" in config_payload["config"]
