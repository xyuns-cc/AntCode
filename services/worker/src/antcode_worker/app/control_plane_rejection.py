"""把控制面对已签名请求的拒绝，翻译成 Worker 侧可执行的运维动作。

Worker 以前只是把服务端的 ``message`` 原样抛成 ``RuntimeError``。控制面库被重建
或回滚到注册之前时，本地凭据结构上仍然合法（字段齐全、格式对），但库里已经没有
这个 worker_id，于是每次启动都在第一次签名请求上收到 ``401 签名验证失败`` 并退出；
容器 ``restart: unless-stopped`` 把它变成永久崩溃循环，而那句文案会把人引向 HMAC
密钥或时钟，离真因极远。

判据只看服务端回包里的结构化 ``data.error_code``，绝不匹配文案：仓里有
``"NOSCRIPT" in str(exc)`` 这样的 P0 前科（错误串被上游改写后判定恒为假）。

**这里刻意不自动重新注册**，原因写在 ``_REREGISTRATION_IS_MANUAL`` 上。
"""

from __future__ import annotations

from typing import Any

from antcode_core.common.security.worker_auth_reasons import WorkerAuthReason

_ERROR_CODE_FIELD = "error_code"

#: 恢复只能人工，不能由 Worker 自动完成，有三条各自独立的理由：
#: 1. 安装 Key 是一次性的：注册 ACK 之后恢复窗口永久关闭（服务端
#:    ``_validate_recovery`` 会回 409），库重建后连 Key 记录本身都不存在（404）。
#:    "被拒就用安装 Key 重注册"在这两种情况下都注定失败，只会把崩溃循环换个文案。
#: 2. 自动重注册会打穿撤销：管理员删除/停用一台 Worker 正是靠把身份从库里去掉，
#:    若它被拒后能自己回来，这条运维手段就废了。
#: 3. 重新注册会换一个新 worker_id，等于静默换身份继续跑——正是禁止的静默 fallback。
_REREGISTRATION_IS_MANUAL = "Worker 不会自动重新注册：安装 Key 一次性，且自动重注册会绕过控制面的停用/删除。"


class ControlPlaneIdentityUnknown(RuntimeError):
    """控制面不认识本地凭据里的 worker_id，本地凭据已永久失效。"""


def control_plane_error(body: Any, status_code: int, *, operation: str, credentials_at: str) -> RuntimeError:
    """由控制面的失败回包构造异常；身份不存在时给出确切的清理路径。

    未携带已知码的失败仍原样上报服务端文案——那是"我们确实不知道更多"，
    而不是退化成一个笼统默认值。
    """
    message = _response_message(body)
    if _error_code(body) == WorkerAuthReason.IDENTITY_UNKNOWN.value:
        return ControlPlaneIdentityUnknown(
            f"{operation}被拒：{message.rstrip('。')}。请清除 {credentials_at} 后用新的安装 Key 重新注册。"
            f"{_REREGISTRATION_IS_MANUAL}"
        )
    return RuntimeError(message or f"{operation}失败 (HTTP {status_code})")


def _response_message(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    return str(body.get("message") or body.get("detail") or "")


def _error_code(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    data = body.get("data")
    if not isinstance(data, dict):
        return ""
    return str(data.get(_ERROR_CODE_FIELD) or "")


__all__ = ["ControlPlaneIdentityUnknown", "control_plane_error"]
