"""把控制面对已签名 **HTTP** 请求的拒绝，翻译成 Worker 侧可执行的运维动作。

控制面库被重建或回滚到注册之前时，本地凭据结构上仍合法但库里已无此 worker_id，
每次启动都在第一次签名请求上收到 ``401 签名验证失败`` 并退出，配合容器
``restart: unless-stopped`` 变成永久崩溃循环，而那句文案会把人引向 HMAC 密钥或时钟。

判据只看回包里的结构化 ``data.error_code``，绝不匹配文案：仓里有
``"NOSCRIPT" in str(exc)`` 这样的 P0 前科（错误串被上游改写后判定恒为假）。

**适用范围仅限走 HMAC 签名的 HTTP 控制面调用**（Direct Redis ACL 签发与注册 ACK）。
**Gateway 的 gRPC 链路不经过这里**：``AuthInterceptor`` 只按 ``verify_api_key`` 的布尔
结果 abort，不区分"身份不存在"与"密钥不对"，也不带 trailing metadata，所以 Gateway
模式的 Worker 在库重建后仍会看到误导文案。要覆盖它得先让 gRPC 侧能携带结构化码。
"""

from __future__ import annotations

from typing import Any

from antcode_core.common.security.worker_auth_reasons import WorkerAuthReason

_ERROR_CODE_FIELD = "error_code"
#: 控制面的拒绝一律以 4xx/5xx 表达；2xx 但 ``success=false`` 是另一类（业务失败），分开处理。
_FIRST_ERROR_STATUS_CODE = 400

#: 恢复只能人工，三条独立理由：
#: 1. 安装 Key 一次性——注册 ACK 后恢复窗口永久关闭（服务端回 409），库重建后连 Key
#:    记录都不存在（404），"被拒就重注册"两种情况都注定失败，只会换个崩溃文案。
#: 2. 自动重注册会打穿撤销：管理员停用 Worker 正是靠把身份从库里去掉。
#: 3. 重注册会换新 worker_id，等于静默换身份继续跑。
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


def require_success_body(response: Any, *, operation: str, credentials_at: str) -> dict[str, Any]:
    """把控制面回包收敛成成功体，失败一律经 ``control_plane_error`` 归因。

    注册 ACK 与 Direct ACL 签发共用这一份：两条链路都在启动路径上、都发已签名请求、都能
    收到同一个 ``WORKER_AUTH_IDENTITY_UNKNOWN``，各写一份就会只有一份接上结构化归因。
    """
    body = _decode_json(response, operation)
    if response.status_code >= _FIRST_ERROR_STATUS_CODE:
        raise control_plane_error(body, response.status_code, operation=operation, credentials_at=credentials_at)
    if not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(_response_message(body) or f"{operation}失败")
    return body


def _decode_json(response: Any, operation: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"{operation}返回非 JSON 响应") from exc


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


__all__ = ["ControlPlaneIdentityUnknown", "control_plane_error", "require_success_body"]
