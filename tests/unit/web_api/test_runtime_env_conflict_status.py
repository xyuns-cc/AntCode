"""显式指定已存在的环境名 → 409 + 结构化码，而不是 500 + 告警噪音。

之前 ``POST /workers/{id}/runtimes`` 把 Worker 的每一种失败都塞进同一个
``HTTPException(500, "创建环境失败")`` 并按 ERROR 记日志：状态码骗人（客户端错误
被报成服务端故障），错误码缺席（调用方只能去猜中文文案），告警被淹。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts.runtime_control_errors import (
    RUNTIME_CONTROL_UNCLASSIFIED,
    RUNTIME_ENV_ALREADY_EXISTS,
)
from antcode_core.application.services.runtime.runtime_control_failures import RuntimeControlFailure
from antcode_web_api.exceptions import http_exception_handler
from antcode_web_api.routes.v1 import runtimes
from fastapi import HTTPException, status

_ENV_NAME = "private-conflict-py311"
_CONFLICT_TEXT = f"虚拟环境 {_ENV_NAME} 已存在"


def _payload() -> SimpleNamespace:
    return SimpleNamespace(
        scope=SimpleNamespace(value="private"),
        python_version="3.11",
        env_name=_ENV_NAME,
        packages=[],
    )


def _patch_route(monkeypatch, result: dict[str, object]) -> None:
    monkeypatch.setattr(
        runtimes,
        "ensure_worker_admin_access",
        AsyncMock(return_value=SimpleNamespace(public_id="worker-1")),
    )
    monkeypatch.setattr(runtimes.runtime_control_service, "create_env", AsyncMock(return_value=result))
    monkeypatch.setattr(runtimes, "audit_runtime_create", AsyncMock())


@pytest.mark.asyncio
async def test_duplicate_env_name_is_a_conflict(monkeypatch) -> None:
    _patch_route(
        monkeypatch,
        {"success": False, "error": _CONFLICT_TEXT, "error_code": RUNTIME_ENV_ALREADY_EXISTS},
    )

    with pytest.raises(HTTPException) as exc:
        await runtimes.create_env(
            "worker-1",
            _payload(),
            current_user_id=10,
            current_user=SimpleNamespace(username="alice"),
        )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert exc.value.error_code == RUNTIME_ENV_ALREADY_EXISTS
    # 「是哪个环境重名」是用户唯一需要的线索，必须留在 detail 里。
    assert exc.value.detail == _CONFLICT_TEXT


@pytest.mark.asyncio
async def test_unclassified_worker_failure_stays_a_500(monkeypatch) -> None:
    """控制组：真正的服务端故障不能被这次改动一起降级成 4xx。"""
    _patch_route(
        monkeypatch,
        {"success": False, "error": "uv venv 失败: disk full", "error_code": RUNTIME_CONTROL_UNCLASSIFIED},
    )

    with pytest.raises(HTTPException) as exc:
        await runtimes.create_env(
            "worker-1",
            _payload(),
            current_user_id=10,
            current_user=SimpleNamespace(username="alice"),
        )

    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc.value.detail == "创建环境失败"


@pytest.mark.asyncio
async def test_conflict_is_not_logged_as_a_server_error(monkeypatch, caplog) -> None:
    """告警噪音的证伪项：调用方过错不该在控制面留下 ERROR 记录。"""
    from loguru import logger

    _patch_route(
        monkeypatch,
        {"success": False, "error": _CONFLICT_TEXT, "error_code": RUNTIME_ENV_ALREADY_EXISTS},
    )
    sink_id = logger.add(caplog.handler, level="INFO", format="{message}")
    try:
        with pytest.raises(HTTPException):
            await runtimes.create_env(
                "worker-1",
                _payload(),
                current_user_id=10,
                current_user=SimpleNamespace(username="alice"),
            )
    finally:
        logger.remove(sink_id)

    assert "ERROR" not in {record.levelname for record in caplog.records}


@pytest.mark.asyncio
async def test_response_body_carries_the_structured_code() -> None:
    """码必须真的到达客户端：只有异常对象上带着不算数。"""
    failure = RuntimeControlFailure(
        status_code=status.HTTP_409_CONFLICT,
        detail=_CONFLICT_TEXT,
        error_code=RUNTIME_ENV_ALREADY_EXISTS,
    )

    response = await http_exception_handler(SimpleNamespace(), failure)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert f'"error_code":"{RUNTIME_ENV_ALREADY_EXISTS}"'.encode() in response.body
