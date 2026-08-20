"""控制面读码、按码定 HTTP 语义，并对缺码的失败回包 fail-closed。

覆盖三段：``_decode_runtime_response`` 取码、``runtime_control_failure`` 定状态码、
项目创建绑定运行时时同样走这条映射（此前它自己另写了一份 500）。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from antcode_contracts.runtime_control_errors import (
    RUNTIME_CONTROL_UNCLASSIFIED,
    RUNTIME_ENV_ALREADY_EXISTS,
)
from antcode_core.application.services.projects.project_runtime_binding import ProjectRuntimeBindingMixin
from antcode_core.application.services.runtime.runtime_control_failures import runtime_control_failure
from fastapi import HTTPException, status

module = importlib.import_module("antcode_core.application.services.runtime.runtime_control_service")

_ENV_NAME = "private-conflict-py311"
_CONFLICT_TEXT = f"虚拟环境 {_ENV_NAME} 已存在"


def _reply(data: str) -> dict[str, str]:
    return {"request_id": "worker-1:x", "success": "false", "data": data, "error": _CONFLICT_TEXT}


def test_failure_reply_exposes_the_structured_code() -> None:
    decoded = module.decode_stream_payload(_reply(f'{{"error_code": "{RUNTIME_ENV_ALREADY_EXISTS}"}}'))

    result = module._decode_runtime_response(decoded)

    assert result["success"] is False
    assert result["error_code"] == RUNTIME_ENV_ALREADY_EXISTS


@pytest.mark.parametrize(
    ("data", "reason"),
    # "null" 就是 v2 Worker 失败回包的原样形态——线协议门禁不放它进来的理由。
    [("null", "不是 object"), ("{}", "缺少结构化 error_code")],
)
def test_codeless_failure_reply_is_rejected_instead_of_guessed(data: str, reason: str) -> None:
    """线协议门禁保证只有同版本 Worker 能拿到 Lease，所以缺码只可能是回包损坏。"""
    decoded = module.decode_stream_payload(_reply(data))

    with pytest.raises(ValueError, match=reason):
        module._decode_runtime_response(decoded)


def test_success_reply_needs_no_code() -> None:
    """控制组：成功回包的 data 是业务结果，不该被要求带码。"""
    decoded = module.decode_stream_payload(
        {"request_id": "worker-1:x", "success": "true", "data": '{"name": "env"}', "error": ""}
    )

    assert module._decode_runtime_response(decoded) == {"success": True, "error": "", "data": {"name": "env"}}


def test_caller_fault_maps_to_conflict_with_the_worker_text() -> None:
    failure = runtime_control_failure(
        "创建环境",
        {"success": False, "error": _CONFLICT_TEXT, "error_code": RUNTIME_ENV_ALREADY_EXISTS},
    )

    assert failure.status_code == status.HTTP_409_CONFLICT
    assert failure.error_code == RUNTIME_ENV_ALREADY_EXISTS
    assert failure.detail == _CONFLICT_TEXT


def test_server_fault_stays_a_500_without_leaking_worker_internals() -> None:
    failure = runtime_control_failure(
        "创建环境",
        {"success": False, "error": "postgresql://user:secret@db", "error_code": RUNTIME_CONTROL_UNCLASSIFIED},
    )

    assert failure.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert failure.detail == "创建环境失败"
    assert "secret" not in failure.detail


@pytest.mark.asyncio
async def test_project_creation_reports_a_conflict_for_a_duplicate_env_name() -> None:
    runtime_service = SimpleNamespace(
        create_env=AsyncMock(
            return_value={"success": False, "error": _CONFLICT_TEXT, "error_code": RUNTIME_ENV_ALREADY_EXISTS}
        )
    )
    runtime = {
        "worker_id": "worker-1",
        "scope": "private",
        "python_version": "3.11",
        "requested_name": _ENV_NAME,
        "dependencies": [],
        "created_by": "alice",
        "owner_user_id": "10",
    }

    with pytest.raises(HTTPException) as exc:
        await ProjectRuntimeBindingMixin._create_worker_environment(
            SimpleNamespace(public_id="project-7"),
            runtime,
            runtime_service,
        )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert exc.value.detail == _CONFLICT_TEXT
