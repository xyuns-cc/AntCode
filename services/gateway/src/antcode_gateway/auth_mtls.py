"""mTLS 证书身份与已认证 Worker 主体的绑定校验。

``intercept_service`` 阶段拿不到 ``ServicerContext``,所以证书校验必须下沉到
实际 RPC 调用阶段:这里把下游 handler 包一层,在业务逻辑之前核对 peer 证书
CN/SAN 与认证主体,并把主体写入 ``AUTHENTICATED_WORKER_ID`` contextvar。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import grpc

from antcode_gateway.auth_principal import AUTHENTICATED_WORKER_ID

# 从 gRPC auth_context 读取 peer 身份时关注的键
PEER_IDENTITY_KEYS = ("x509_common_name", "x509_subject_alternative_name")

MISSING_CERTIFICATE_REASON = "mTLS 认证未提供客户端证书身份"
IDENTITY_MISMATCH_REASON = "mTLS 证书身份与 worker_id 不匹配"
AUTH_CONTEXT_UNREADABLE_REASON = "无法读取 mTLS auth_context"


def check_mtls_binding(
    context: grpc.aio.ServicerContext,
    authenticated_worker_id: str,
    *,
    require_certificate: bool = False,
) -> tuple[bool, str]:
    """从 ``context.auth_context()`` 读 peer CN/SAN,和认证主体对齐。

    返回 ``(ok, reason)``:
    - 没有 authenticated_worker_id(不是 worker 发起的请求): 放行,由业务层校验。
    - 没有 peer 身份且不强制要求证书(开发环境无 mTLS): 放行。
    - 有 peer 身份但与 worker_id 不一致: 拒绝。
    """
    if not authenticated_worker_id:
        return True, ""
    identities, read_error = _read_peer_identities(context)
    if read_error:
        return False, read_error
    if not identities:
        if require_certificate:
            return False, MISSING_CERTIFICATE_REASON
        return True, ""
    if _identity_matches_worker(identities, authenticated_worker_id):
        return True, ""
    return False, IDENTITY_MISMATCH_REASON


def _read_peer_identities(context: grpc.aio.ServicerContext) -> tuple[list[str], str]:
    try:
        auth_ctx = context.auth_context() or {}
    except Exception:  # noqa: BLE001 - 读不到证书上下文时必须拒绝,不能放行
        return [], AUTH_CONTEXT_UNREADABLE_REASON
    return _peer_identities(auth_ctx), ""


def _identity_matches_worker(identities: list[str], authenticated_worker_id: str) -> bool:
    """精确匹配证书身份（P2 §4.2：禁止宽松后缀匹配）。

    允许的形态：
    - CN/SAN 完整相等: ``<worker_id>``
    - 前缀标记整串相等: ``worker:<worker_id>``
    - URI SAN: 解析后 path 最后一段精确等于 ``<worker_id>``。
    此前 ``endswith("worker:<id>")`` 会被 ``evilworker:<id>`` 这类拼接身份绕过。
    """
    for identity in identities:
        if identity in (authenticated_worker_id, f"worker:{authenticated_worker_id}"):
            return True
        if "://" in identity:
            segments = [segment for segment in urlsplit(identity).path.split("/") if segment]
            if segments and segments[-1] == authenticated_worker_id:
                return True
    return False


def _peer_identities(auth_context: dict) -> list[str]:
    identities: list[str] = []
    for key in PEER_IDENTITY_KEYS:
        for raw in auth_context.get(key, []) or []:
            value = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
            identities.append(value)
    return identities


async def _require_mtls_binding(context: Any, worker_id: str, require_certificate: bool) -> None:
    ok, reason = check_mtls_binding(context, worker_id, require_certificate=require_certificate)
    if not ok:
        await context.abort(grpc.StatusCode.PERMISSION_DENIED, reason)


def make_mtls_wrapped_handler(
    original: grpc.RpcMethodHandler,
    authenticated_worker_id: str,
    *,
    require_certificate: bool = False,
) -> grpc.RpcMethodHandler:
    """按 RPC 形态包一层证书校验 + 主体 contextvar 注入。"""
    if original.unary_unary is not None:
        return _wrap_unary_unary(original, authenticated_worker_id, require_certificate)
    if original.unary_stream is not None:
        return _wrap_unary_stream(original, authenticated_worker_id, require_certificate)
    if original.stream_unary is not None:
        return _wrap_stream_unary(original, authenticated_worker_id, require_certificate)
    if original.stream_stream is not None:
        return _wrap_stream_stream(original, authenticated_worker_id, require_certificate)
    return original


def _wrap_unary_unary(original: Any, worker_id: str, require_certificate: bool) -> grpc.RpcMethodHandler:
    async def wrapped(request, context):
        await _require_mtls_binding(context, worker_id, require_certificate)
        token = AUTHENTICATED_WORKER_ID.set(worker_id)
        try:
            return await original.unary_unary(request, context)
        finally:
            AUTHENTICATED_WORKER_ID.reset(token)

    return grpc.unary_unary_rpc_method_handler(
        wrapped,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


def _wrap_unary_stream(original: Any, worker_id: str, require_certificate: bool) -> grpc.RpcMethodHandler:
    async def wrapped(request, context):
        await _require_mtls_binding(context, worker_id, require_certificate)
        token = AUTHENTICATED_WORKER_ID.set(worker_id)
        try:
            async for response in original.unary_stream(request, context):
                yield response
        finally:
            AUTHENTICATED_WORKER_ID.reset(token)

    return grpc.unary_stream_rpc_method_handler(
        wrapped,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


def _wrap_stream_unary(original: Any, worker_id: str, require_certificate: bool) -> grpc.RpcMethodHandler:
    async def wrapped(request_iterator, context):
        await _require_mtls_binding(context, worker_id, require_certificate)
        token = AUTHENTICATED_WORKER_ID.set(worker_id)
        try:
            return await original.stream_unary(request_iterator, context)
        finally:
            AUTHENTICATED_WORKER_ID.reset(token)

    return grpc.stream_unary_rpc_method_handler(
        wrapped,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


def _wrap_stream_stream(original: Any, worker_id: str, require_certificate: bool) -> grpc.RpcMethodHandler:
    async def wrapped(request_iterator, context):
        await _require_mtls_binding(context, worker_id, require_certificate)
        token = AUTHENTICATED_WORKER_ID.set(worker_id)
        try:
            async for response in original.stream_stream(request_iterator, context):
                yield response
        finally:
            AUTHENTICATED_WORKER_ID.reset(token)

    return grpc.stream_stream_rpc_method_handler(
        wrapped,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


__all__ = ["check_mtls_binding", "make_mtls_wrapped_handler"]
