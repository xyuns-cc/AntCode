"""运行时控制失败的结构化错误码 —— 控制面判定失败归属的唯一依据。

错误消息是给人看的中文原文：会被改写、会被 ``normalize_persisted_error_message``
脱敏截断，还随时可能翻译。拿它做程序判定在本仓有过 P0 前科（``"NOSCRIPT" in str(exc)``
因为 redis-py 会剥掉错误码前缀而恒为假，那行是死代码，一次 Redis 重启就让全集群
Worker 永久掉线）。所以失败归属必须由 **异常类型** 产出稳定码，随回包过线，控制面
只读码、不读文案。

码放进已有的 ``data`` 字段，而不是新增顶层 wire 字段：``data`` 在两种传输里都是逐字节
透传的 JSON（Direct 走 reply Stream 的 ``data``，Gateway 走 ``AckControlRequest.data_json``），
且两侧的 settlement fingerprint 本来就把 ``data`` 纳入哈希，幂等语义无需另行扩展。
成功回包的 ``data`` 是业务结果，失败回包的 ``data`` 之前恒为 ``None``，正好空出来。

分类是**全覆盖**的：没有专门码的失败落到 ``RUNTIME_CONTROL_UNCLASSIFIED``，那是一个
明确的"服务端故障"判定，不是"猜一个码"。控制面对缺码的失败回包 fail-closed。
"""

from __future__ import annotations

from typing import Any

#: 失败回包 ``data`` 里承载错误码的键名。
RUNTIME_CONTROL_ERROR_CODE_FIELD = "error_code"

#: 目标环境名已被占用；重试同一请求永远不会成功，必须换名字。
RUNTIME_ENV_ALREADY_EXISTS = "RUNTIME_ENV_ALREADY_EXISTS"

#: 没有专门码的 Worker 侧失败，一律按服务端故障处理。
RUNTIME_CONTROL_UNCLASSIFIED = "RUNTIME_CONTROL_UNCLASSIFIED"

#: 控制面在等回包时超时——Worker 从未答复，与 Worker 上报的失败区分开。
RUNTIME_CONTROL_TIMEOUT = "RUNTIME_CONTROL_TIMEOUT"

#: reply stream 有条目但没有消息体，属于控制通道损坏。
RUNTIME_CONTROL_EMPTY_REPLY = "RUNTIME_CONTROL_EMPTY_REPLY"

#: 由调用方输入决定的失败：不是服务端故障，既不该按 5xx 回，也不该进 ERROR 告警。
RUNTIME_CONTROL_CALLER_FAULT_CODES = frozenset({RUNTIME_ENV_ALREADY_EXISTS})


class RuntimeControlCodedError(Exception):
    """携带结构化错误码的运行时控制失败。"""

    error_code = RUNTIME_CONTROL_UNCLASSIFIED


class RuntimeEnvAlreadyExistsError(RuntimeControlCodedError):
    """目标虚拟环境名已被占用。"""

    error_code = RUNTIME_ENV_ALREADY_EXISTS


def runtime_control_failure_data(exc: BaseException) -> dict[str, str]:
    """失败回包的 ``data``：每一次失败都带码，未分类也是一个明确的码。"""
    code = exc.error_code if isinstance(exc, RuntimeControlCodedError) else RUNTIME_CONTROL_UNCLASSIFIED
    return {RUNTIME_CONTROL_ERROR_CODE_FIELD: code}


def require_runtime_control_error_code(data: Any) -> str:
    """从失败回包里取码；缺码即协议违约。

    fail-closed 而不是补一个默认码：线协议门禁保证只有同版本 Worker 能拿到 Lease，
    所以缺码只可能是回包损坏或伪造，猜一个码会把损坏伪装成一次普通失败。
    """
    if not isinstance(data, dict):
        raise ValueError("运行时控制失败回包 data 不是 object")
    code = data.get(RUNTIME_CONTROL_ERROR_CODE_FIELD)
    if not isinstance(code, str) or not code:
        raise ValueError("运行时控制失败回包缺少结构化 error_code")
    return code


__all__ = [
    "RUNTIME_CONTROL_CALLER_FAULT_CODES",
    "RUNTIME_CONTROL_EMPTY_REPLY",
    "RUNTIME_CONTROL_ERROR_CODE_FIELD",
    "RUNTIME_CONTROL_TIMEOUT",
    "RUNTIME_CONTROL_UNCLASSIFIED",
    "RUNTIME_ENV_ALREADY_EXISTS",
    "RuntimeControlCodedError",
    "RuntimeEnvAlreadyExistsError",
    "require_runtime_control_error_code",
    "runtime_control_failure_data",
]
