"""Recoverable V2 registration for Worker bootstrap credentials."""

from __future__ import annotations

import os
import secrets
import time
from types import SimpleNamespace
from typing import Any

import httpx
from antcode_core.common.utils.worker_request import (
    HTTP_POST_METHOD,
    build_worker_signed_headers,
    encode_worker_json_body,
    request_path_from_url,
)
from loguru import logger

from antcode_worker.app.control_plane_rejection import require_success_body
from antcode_worker.app.http_trust import certificate_authority
from antcode_worker.services.credential import WorkerCredentials
from antcode_worker.services.credential.registration_intent import (
    RegistrationIntent,
    RegistrationRequest,
)

_HTTP_TIMEOUT_SECONDS = 15.0
_REGISTRATION_PROTOCOL_VERSION = 2


def register_by_install_key(config: Any, credential_service: Any) -> WorkerCredentials | None:
    worker_key = getattr(config, "worker_key", "") or os.getenv("ANTCODE_WORKER_KEY") or None
    credential_service.ensure_durable_writable()
    request = _registration_request(config) if worker_key else None
    with credential_service.registration_session(worker_key, request) as intent:
        if intent is None:
            return None
        existing = credential_service.load()
        if existing is not None:
            _ack_and_finish(intent, existing, credential_service)
            return existing
        credentials = _send_registration(intent, credential_service.store.describe_location())
        credential_service.save(credentials)
        _ack_and_finish(intent, credentials, credential_service)
        logger.info("安装 Key V2 注册成功且已确认: worker_id={}", credentials.worker_id)
        return credentials


def resume_registration_ack(credential_service: Any, credentials: WorkerCredentials | None) -> None:
    if credentials is None or not credentials.is_valid():
        return
    with credential_service.registration_session() as intent:
        if intent is not None:
            _ack_and_finish(intent, credentials, credential_service)


def normalize_api_base_url(
    value: str | None,
    gateway_host: str,
    *,
    allow_insecure_internal: bool = False,
) -> str:
    from urllib.parse import urlparse

    url = (value or "").strip()
    if not url:
        url = f"http://{gateway_host}:8000"
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    normalized = url.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Worker 控制面 API 地址必须是有效的 HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Worker 控制面 API 地址不允许凭证、query 或 fragment")
    if parsed.scheme == "http" and not _allow_plain_http(parsed.hostname, allow_insecure_internal):
        raise ValueError("远程 Worker 控制面 API 必须使用 HTTPS")
    return normalized


def _registration_request(config: Any) -> RegistrationRequest:
    gateway_host = getattr(config, "gateway_host", "localhost")
    api_base_url = normalize_api_base_url(
        getattr(config, "api_base_url", "") or os.getenv("WORKER_API_BASE_URL"),
        gateway_host,
        allow_insecure_internal=getattr(config, "api_allow_insecure_internal", False),
    )
    host = getattr(config, "host", "")
    if host in {"", "0.0.0.0", "127.0.0.1", "localhost"}:
        from antcode_worker.config import get_local_ip

        host = get_local_ip()
    return RegistrationRequest(
        name=getattr(config, "name", "Worker-001"),
        host=host,
        port=getattr(config, "port", 8001),
        region=getattr(config, "region", ""),
        transport_mode=getattr(config, "transport_mode", "gateway"),
        api_base_url=api_base_url,
        gateway_host=gateway_host,
        gateway_port=getattr(config, "gateway_port", 50051),
    )


def _send_registration(intent: RegistrationIntent, credentials_at: str) -> WorkerCredentials:
    request = intent.request
    payload = {
        "key": intent.install_key,
        "name": request.name,
        "host": request.host,
        "port": request.port,
        "region": request.region,
        "transport_mode": request.transport_mode,
        "client_timestamp": int(time.time()),
        "client_nonce": secrets.token_hex(16),
        "registration_id": intent.registration_id,
        "recovery_secret": intent.recovery_secret,
    }
    url = f"{request.api_base_url}/api/v1/workers/register-by-key-v2"
    response = _post_json(url, encode_worker_json_body(payload))
    data = _require_registration_data(response, operation="安装 Key V2 注册", credentials_at=credentials_at)
    _validate_registration_response(data, intent.registration_id)
    return WorkerCredentials(
        worker_id=str(data["worker_id"]),
        api_key=str(data["api_key"]),
        secret_key=str(data["secret_key"]),
        gateway_host=request.gateway_host,
        gateway_port=request.gateway_port,
        registration_id=intent.registration_id,
    )


def _ack_and_finish(
    intent: RegistrationIntent,
    credentials: WorkerCredentials,
    credential_service: Any,
) -> None:
    if credentials.registration_id != intent.registration_id:
        raise RuntimeError("本地凭据与待确认注册意图不匹配")
    payload = {"registration_id": intent.registration_id}
    body = encode_worker_json_body(payload)
    url = f"{intent.request.api_base_url}/api/v1/workers/{credentials.worker_id}/registration-ack"
    headers = build_worker_signed_headers(
        SimpleNamespace(public_id=credentials.worker_id),
        api_key=credentials.api_key,
        secret_key=credentials.secret_key,
        method=HTTP_POST_METHOD,
        path=request_path_from_url(url),
        body=body,
    )
    response = _post_json(url, body, headers=headers)
    _require_registration_data(
        response,
        operation="Worker 注册 ACK",
        credentials_at=credential_service.store.describe_location(),
    )
    credential_service.finish_registration()


def _post_json(url: str, body: bytes, *, headers: dict[str, str] | None = None) -> httpx.Response:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    client = httpx.Client(
        timeout=_HTTP_TIMEOUT_SECONDS,
        trust_env=_should_trust_env_proxy(url),
        verify=certificate_authority(),
    )
    with client:
        return client.post(url, content=body, headers=request_headers)


def _require_registration_data(response: httpx.Response, *, operation: str, credentials_at: str) -> dict[str, Any]:
    """注册链路同样经 ``require_success_body`` 归因。

    注册 ACK 是**已签名**请求，且 ``resume_registration_ack`` 在每次启动都跑（两种
    传输模式都跑）：注册后、ACK 前崩溃过的 Worker 会留下意图文件，控制面库随后被
    重建，这次 ACK 就会拿到 ``WORKER_AUTH_IDENTITY_UNKNOWN``。这里以前只把服务端
    文案原样抛出，丢掉了结构化码，也就报不出该清哪一份本地凭据。
    """
    body = require_success_body(response, operation=operation, credentials_at=credentials_at)
    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"{operation}返回数据不完整")
    return data


def _validate_registration_response(data: dict[str, Any], registration_id: str) -> None:
    if data.get("protocol_version") != _REGISTRATION_PROTOCOL_VERSION:
        raise RuntimeError("安装 Key V2 注册协议版本不匹配")
    if data.get("registration_id") != registration_id:
        raise RuntimeError("安装 Key V2 注册响应 registration_id 不匹配")
    if not all(data.get(field) for field in ("worker_id", "api_key", "secret_key")):
        raise RuntimeError("安装 Key V2 注册返回凭据不完整")
    if not isinstance(data.get("recovered"), bool):
        raise RuntimeError("安装 Key V2 注册返回 recovered 无效")
    if not isinstance(data.get("recovery_expires_at"), str) or not data["recovery_expires_at"]:
        raise RuntimeError("安装 Key V2 注册返回恢复期限无效")


def _should_trust_env_proxy(url: str) -> bool:
    from urllib.parse import urlparse

    host = urlparse(url).hostname
    if not host or host.lower() in {"localhost", "127.0.0.1", "::1"}:
        return False
    return "." in host or ":" in host


def _allow_plain_http(host: str, allow_insecure_internal: bool) -> bool:
    import ipaddress

    lowered = host.lower()
    if lowered == "localhost":
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return allow_insecure_internal and "." not in lowered


__all__ = ["normalize_api_base_url", "register_by_install_key", "resume_registration_ack"]
