"""把 Worker 运行时控制失败翻译成 HTTP 语义。

两条线严格分开：``error_code`` 是程序唯一可判定的契约，中文 ``error`` 只是给人看的
原文。状态码只由码决定，绝不匹配文案。

Worker 侧的运行时管理入口不止一个（环境管理页的 ``POST /workers/{id}/runtimes``、
项目创建时的运行时绑定），它们此前各写各的 500，行为已经分叉：一个把 Worker 原文
原样回给用户，另一个只回"创建环境失败"。这里收成一处，避免下一次再分叉。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from antcode_contracts.runtime_control_errors import (
    RUNTIME_CONTROL_CALLER_FAULT_CODES,
    RUNTIME_ENV_ALREADY_EXISTS,
)
from fastapi import HTTPException, status

#: 调用方过错 → 对应的 4xx。键集必须覆盖 ``RUNTIME_CONTROL_CALLER_FAULT_CODES``；
#: 漏一个就在 ``runtime_control_failure`` 里 KeyError，不会静默退回 500。
_CALLER_FAULT_STATUS = {RUNTIME_ENV_ALREADY_EXISTS: status.HTTP_409_CONFLICT}


class RuntimeControlFailure(HTTPException):
    """带结构化 ``error_code`` 的运行时控制失败。

    ``error_code`` 由 web_api 的 HTTP 异常处理器透出到响应体的 ``data.error_code``，
    调用方无需解析中文文案就能判定失败类型。
    """

    def __init__(self, *, status_code: int, detail: str, error_code: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code


def runtime_control_failure(operation: str, result: Mapping[str, Any]) -> RuntimeControlFailure:
    """由失败结果构造 HTTP 异常；``result`` 必须带 ``error_code``。

    直接下标取码而不是 ``get``：控制面对失败回包已经 fail-closed 要求带码，缺码
    只可能是调用方绕过了 ``RuntimeControlService``，那要立刻炸而不是猜一个 500。
    """
    error_code = str(result["error_code"])
    if error_code not in RUNTIME_CONTROL_CALLER_FAULT_CODES:
        # 服务端故障不把 Worker 内部细节回传给用户，仍只留在日志里。
        return RuntimeControlFailure(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{operation}失败",
            error_code=error_code,
        )
    # 调用方过错才回原文：它是"到底是哪个环境重名"的唯一线索。
    return RuntimeControlFailure(
        status_code=_CALLER_FAULT_STATUS[error_code],
        detail=str(result.get("error") or ""),
        error_code=error_code,
    )


__all__ = ["RuntimeControlFailure", "runtime_control_failure"]
