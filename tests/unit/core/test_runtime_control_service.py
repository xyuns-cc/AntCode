from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts.runtime_metadata import RUNTIME_DESCRIPTION_MAX_BYTES

module = importlib.import_module("antcode_core.application.services.runtime.runtime_control_service")
RuntimeControlService = module.RuntimeControlService
_REQUEST_NONCE = "a" * 32
_REQUEST_ID = f"worker-1:{_REQUEST_NONCE}"
_REPLY_STREAM = f"antcode:control:reply:{_REQUEST_ID}"


class RuntimeRedis:
    def __init__(self, response: dict[str, str]) -> None:
        self.response = response
        self.xadd = AsyncMock(return_value="1-0")

    async def time(self):
        # P1-DR-04: deadline 以 Redis 服务端时钟为准（TIME 返回 [s, us]）。
        return [1_700_000_000, 0]

    async def xread(self, streams, *, count, block):
        _ = count, block
        stream = next(iter(streams))
        return [(stream, [("2-0", self.response)])]


def _fixed_request_id(monkeypatch) -> None:
    monkeypatch.setattr(module.uuid, "uuid4", MagicMock(return_value=MagicMock(hex=_REQUEST_NONCE)))


@pytest.mark.asyncio
async def test_runtime_response_validates_request_id_and_keeps_ttl_evidence(monkeypatch):
    redis = RuntimeRedis(
        {
            "request_id": _REQUEST_ID,
            "success": "true",
            "data": "null",
            "error": "",
        }
    )
    _fixed_request_id(monkeypatch)
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=redis))

    result = await RuntimeControlService().send_command("worker-1", "get_platform_info")

    assert result == {"success": True, "error": "", "data": None}
    payload = redis.xadd.await_args.args[1]
    assert payload["request_id"] == _REQUEST_ID
    assert payload["reply_stream"] == _REPLY_STREAM
    assert payload["expires_at_ms"] == "1700000030000"
    assert not hasattr(redis, "delete")


@pytest.mark.asyncio
async def test_runtime_command_rejects_non_positive_timeout_before_redis(monkeypatch):
    get_redis = AsyncMock()
    monkeypatch.setattr(module, "get_redis_client", get_redis)

    with pytest.raises(ValueError, match="timeout 必须大于 0"):
        await RuntimeControlService().send_command("worker-1", "list_envs", timeout=0)

    get_redis.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_id", ["", " worker-1", "worker:1", "worker*1", "x" * 33])
async def test_runtime_command_rejects_worker_ids_unsafe_for_acl_keys(monkeypatch, worker_id):
    get_redis = AsyncMock()
    monkeypatch.setattr(module, "get_redis_client", get_redis)

    with pytest.raises(ValueError, match="worker_id"):
        await RuntimeControlService().send_command(worker_id, "list_envs")

    get_redis.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_response_rejects_mismatched_request_id(monkeypatch):
    redis = RuntimeRedis(
        {
            "request_id": "forged-request",
            "success": "true",
            "data": "{}",
            "error": "",
        }
    )
    _fixed_request_id(monkeypatch)
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=redis))

    with pytest.raises(RuntimeError, match="request_id 不匹配"):
        await RuntimeControlService().send_command("worker-1", "list_envs")


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["", "not-json"])
async def test_runtime_response_requires_valid_data_json(monkeypatch, data):
    redis = RuntimeRedis(
        {
            "request_id": _REQUEST_ID,
            "success": "false",
            "data": data,
            "error": "failed",
        }
    )
    _fixed_request_id(monkeypatch)
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=redis))

    with pytest.raises((RuntimeError, ValueError)):
        await RuntimeControlService().send_command("worker-1", "get_env")


@pytest.mark.asyncio
async def test_runtime_response_rejects_non_boolean_success(monkeypatch):
    redis = RuntimeRedis({"request_id": _REQUEST_ID, "success": "yes", "data": "null", "error": ""})
    _fixed_request_id(monkeypatch)
    monkeypatch.setattr(module, "get_redis_client", AsyncMock(return_value=redis))

    with pytest.raises(RuntimeError, match="success 字段无效"):
        await RuntimeControlService().send_command("worker-1", "get_env")


@pytest.mark.asyncio
async def test_create_env_transports_stable_owner_user_id() -> None:
    service = RuntimeControlService()
    service.send_command = AsyncMock(return_value={"success": True})

    await service.create_env(
        "worker-1",
        "private-env",
        "3.11",
        packages=[],
        created_by="alice",
        owner_user_id="7",
    )

    service.send_command.assert_awaited_once_with(
        "worker-1",
        "create_env",
        {
            "env_name": "private-env",
            "python_version": "3.11",
            "packages": [],
            "created_by": "alice",
            "owner_user_id": "7",
        },
        timeout=600,
    )


@pytest.mark.asyncio
async def test_update_env_rejects_oversized_metadata_before_redis_write() -> None:
    service = RuntimeControlService()
    service.send_command = AsyncMock()

    with pytest.raises(ValueError, match="description UTF-8"):
        await service.update_env(
            "worker-1",
            "private-env",
            description="界" * (RUNTIME_DESCRIPTION_MAX_BYTES // 3 + 1),
        )

    service.send_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_env_rejects_invalid_owner_before_redis_write() -> None:
    service = RuntimeControlService()
    service.send_command = AsyncMock()

    with pytest.raises(ValueError, match="owner_user_id"):
        await service.create_env("worker-1", "private-env", "3.11", owner_user_id="not-an-id")

    service.send_command.assert_not_awaited()
