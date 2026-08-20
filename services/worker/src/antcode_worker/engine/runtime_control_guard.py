"""P1-DR-04: 运行时控制事件的 deadline 判定（权威时钟由 transport 提供）。

外加运行时 action 失败的归类：同一次分类同时决定回包里的结构化错误码和本地日志
级别，所以放在一处——分开写迟早会出现"回包说是调用方过错、日志仍按 ERROR 报警"。
"""

from __future__ import annotations

from typing import Any

from antcode_contracts.runtime_control_errors import (
    RUNTIME_CONTROL_CALLER_FAULT_CODES,
    RUNTIME_CONTROL_ERROR_CODE_FIELD,
    runtime_control_failure_data,
)
from loguru import logger


def _require_live_runtime_control(payload: dict[str, Any], now_ms: int) -> None:
    """``now_ms`` 必须来自 transport 权威时钟（Direct=Redis TIME），与
    Master 生成 deadline 的时钟一致，消除跨机器 wall clock 偏移。"""
    try:
        expires_at_ms = int(payload.get("expires_at_ms") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("运行时控制事件 expires_at_ms 无效") from exc
    if expires_at_ms <= 0:
        raise RuntimeError("运行时控制事件缺少 expires_at_ms")
    if now_ms >= expires_at_ms:
        raise RuntimeError("运行时控制事件已过期，拒绝执行")


def runtime_action_failure(exc: BaseException, *, action: str, request_id: str) -> dict[str, str]:
    """产出失败回包的 ``data`` 并按故障归属落日志。

    调用方过错（例如显式指定了一个已存在的环境名）重试多少次都一样，不是本机
    故障：按 ERROR + 堆栈报出去只会淹没真正需要人介入的告警。服务端故障仍然走
    ``logger.exception`` 保留堆栈——AGENTS 的"全暴露"针对的是后者。
    """
    failure = runtime_control_failure_data(exc)
    code = failure[RUNTIME_CONTROL_ERROR_CODE_FIELD]
    if code in RUNTIME_CONTROL_CALLER_FAULT_CODES:
        logger.info("runtime action 被拒: action={} req={} code={}", action, request_id, code)
        return failure
    logger.opt(exception=exc).error("runtime action 失败: action={} req={} code={}", action, request_id, code)
    return failure


__all__ = ["_require_live_runtime_control", "runtime_action_failure"]
