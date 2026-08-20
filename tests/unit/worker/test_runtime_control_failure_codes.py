"""Worker 侧运行时控制失败必须带结构化错误码。

锁住的不变量：失败回包的 ``data`` 恒为 ``{"error_code": ...}``，码来自异常**类型**
而不是错误文案。控制面据此把"环境重名"判成 409，其余判成 500；文案随时会被改写、
翻译或脱敏截断，拿它做判定在本仓有过 P0 前科。

同一次分类还决定日志级别：调用方过错重试多少次都一样，按 ERROR 报只会淹没告警。
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_contracts import control_pb2
from antcode_contracts.runtime_control_errors import (
    RUNTIME_CONTROL_UNCLASSIFIED,
    RUNTIME_ENV_ALREADY_EXISTS,
    RuntimeEnvAlreadyExistsError,
)
from antcode_worker.engine.engine import Engine
from antcode_worker.runtime.uv_manager import UVManager
from antcode_worker.transport.gateway.transport import GatewayConfig, GatewayTransport
from loguru import logger

FUTURE_RUNTIME_DEADLINE_MS = 4_102_444_800_000
_ENV_NAME = "private-conflict-py311"


def _transport() -> MagicMock:
    transport = MagicMock()
    transport.send_control_result = AsyncMock(return_value=True)
    transport.ack_control = AsyncMock(return_value=True)
    transport.authoritative_now_ms = AsyncMock(return_value=1_000)
    transport._lease_id = "lease-test"
    transport._worker_id = "worker-test"
    return transport


def _control(action: str) -> MagicMock:
    return MagicMock(
        payload={
            "action": action,
            "request_id": "req-1",
            "expires_at_ms": FUTURE_RUNTIME_DEADLINE_MS,
            "reply_stream": "reply-stream",
            "payload": {"env_name": _ENV_NAME, "python_version": "3.11"},
        },
        receipt="receipt-1",
    )


@pytest.mark.asyncio
async def test_create_env_on_an_existing_name_raises_the_coded_error(tmp_path) -> None:
    manager = UVManager()
    manager.set_venvs_dir(str(tmp_path))
    os.makedirs(tmp_path / _ENV_NAME)

    with pytest.raises(RuntimeEnvAlreadyExistsError) as exc:
        await manager.create_env(_ENV_NAME, "3.11", created_by="alice", owner_user_id="10")

    assert exc.value.error_code == RUNTIME_ENV_ALREADY_EXISTS
    # 文案仍然点名是哪个环境；它只进 detail，不参与任何判定。
    assert _ENV_NAME in str(exc.value)


@pytest.mark.asyncio
async def test_runtime_control_failure_reply_carries_the_caller_fault_code(monkeypatch) -> None:
    transport = _transport()
    monkeypatch.setattr(
        "antcode_worker.runtime.uv_manager.uv_manager.create_env",
        AsyncMock(side_effect=RuntimeEnvAlreadyExistsError(f"虚拟环境 {_ENV_NAME} 已存在")),
    )
    engine = Engine(transport=transport, executor=MagicMock())

    await engine._handle_runtime_control(_control("create_env"))

    reply = transport.send_control_result.await_args.kwargs
    assert reply["success"] is False
    assert reply["data"] == {"error_code": RUNTIME_ENV_ALREADY_EXISTS}


@pytest.mark.asyncio
async def test_unclassified_runtime_failure_still_carries_a_code(monkeypatch) -> None:
    """分类必须全覆盖：没有专门码的失败也要带码，控制面才能对缺码 fail-closed。"""
    transport = _transport()
    monkeypatch.setattr(
        "antcode_worker.runtime.uv_manager.uv_manager.create_env",
        AsyncMock(side_effect=RuntimeError("uv venv 失败: disk full")),
    )
    engine = Engine(transport=transport, executor=MagicMock())

    await engine._handle_runtime_control(_control("create_env"))

    reply = transport.send_control_result.await_args.kwargs
    assert reply["success"] is False
    assert reply["data"] == {"error_code": RUNTIME_CONTROL_UNCLASSIFIED}


@pytest.mark.asyncio
async def test_successful_action_data_is_untouched(monkeypatch) -> None:
    """控制组：成功回包仍是业务结果本身，错误码只占用失败时空着的 data。"""
    transport = _transport()
    monkeypatch.setattr(
        "antcode_worker.runtime.uv_manager.uv_manager.create_env",
        AsyncMock(return_value={"name": _ENV_NAME}),
    )
    engine = Engine(transport=transport, executor=MagicMock())

    await engine._handle_runtime_control(_control("create_env"))

    reply = transport.send_control_result.await_args.kwargs
    assert reply["success"] is True
    assert reply["data"] == {"name": _ENV_NAME}


@pytest.mark.asyncio
async def test_gateway_serialization_downgrade_also_carries_a_code() -> None:
    """Gateway 传输把不可序列化的结果降级成失败结算，那也是失败回包，必须带码。"""
    stub = MagicMock(AckControl=AsyncMock(return_value=control_pb2.AckControlResponse(received=True)))
    transport = GatewayTransport(gateway_config=GatewayConfig(worker_id="worker-1"))
    transport._running = True
    transport._control_stub = stub

    sent = await transport.send_control_result(
        "request-1",
        "ignored",
        True,
        receipt="antcode:control:worker-1|1-0",
        data={"ts": object()},  # 不可 JSON 序列化
    )

    assert sent is True
    request = stub.AckControl.await_args.args[0]
    assert request.success is False
    assert json.loads(request.data_json) == {"error_code": RUNTIME_CONTROL_UNCLASSIFIED}


@pytest.mark.asyncio
async def test_caller_fault_is_not_logged_as_a_server_error(monkeypatch, caplog) -> None:
    """告警噪音的证伪项：环境重名不得留下 ERROR 记录。"""
    transport = _transport()
    monkeypatch.setattr(
        "antcode_worker.runtime.uv_manager.uv_manager.create_env",
        AsyncMock(side_effect=RuntimeEnvAlreadyExistsError(f"虚拟环境 {_ENV_NAME} 已存在")),
    )
    engine = Engine(transport=transport, executor=MagicMock())
    sink_id = logger.add(caplog.handler, level="INFO", format="{message}")
    try:
        await engine._handle_runtime_control(_control("create_env"))
    finally:
        logger.remove(sink_id)

    levels = {record.levelname for record in caplog.records}
    assert "ERROR" not in levels
    assert any(RUNTIME_ENV_ALREADY_EXISTS in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_server_fault_keeps_the_error_log(monkeypatch, caplog) -> None:
    """控制组：真正的服务端故障仍按 ERROR 报，静音只针对调用方过错。"""
    transport = _transport()
    monkeypatch.setattr(
        "antcode_worker.runtime.uv_manager.uv_manager.create_env",
        AsyncMock(side_effect=RuntimeError("uv venv 失败: disk full")),
    )
    engine = Engine(transport=transport, executor=MagicMock())
    sink_id = logger.add(caplog.handler, level="INFO", format="{message}")
    try:
        await engine._handle_runtime_control(_control("create_env"))
    finally:
        logger.remove(sink_id)

    assert "ERROR" in {record.levelname for record in caplog.records}
