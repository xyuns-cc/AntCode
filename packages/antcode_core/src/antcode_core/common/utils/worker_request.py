"""Worker HTTP request serialization and signing helpers."""

from typing import Any
from urllib.parse import urlsplit

from antcode_core.common.security import canonicalize_http_path, generate_hmac_signature
from antcode_core.common.security.hmac_utils import json_dumps_compact

HTTP_POST_METHOD = "POST"


def encode_worker_json_body(payload: dict[str, Any]) -> bytes:
    """Serialize the exact JSON bytes that will be signed and transmitted."""
    return json_dumps_compact(payload).encode("utf-8")


def request_path_from_url(url: str) -> str:
    """Extract and canonicalize the path component of a final request URL."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Worker 请求 URL 无效")
    return canonicalize_http_path(parsed.path)


def build_worker_signed_headers(
    worker,
    *,
    api_key: str,
    secret_key: str,
    method: str,
    path: str,
    body: bytes,
) -> dict[str, str]:
    """Build credentials and an endpoint-bound Worker HMAC signature."""
    if not worker or not getattr(worker, "public_id", None):
        raise ValueError("Worker 标识缺失")
    if not api_key:
        raise ValueError("Worker API Key 缺失")
    if not secret_key:
        raise ValueError("Worker HMAC 密钥缺失")
    signature_headers = generate_hmac_signature(
        body,
        secret_key,
        method=method,
        path=path,
    )
    return {
        "Authorization": f"Bearer {api_key}",
        "X-Worker-ID": worker.public_id,
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
        **signature_headers,
    }
