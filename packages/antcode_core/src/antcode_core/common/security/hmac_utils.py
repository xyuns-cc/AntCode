"""HMAC helpers for endpoint-bound Worker HTTP requests."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import ujson

WORKER_HTTP_SIGNATURE_VERSION = "2"
WORKER_HTTP_SIGNATURE_HEADER = "X-Signature-Version"
_SIGNATURE_DOMAIN = "antcode-worker-http-hmac-v2"
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_HTTP_METHOD_PATTERN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Z-]+")
_PATH_SAFE_CHARACTERS = "/:@!$&'()*+,;=-._~"


def json_dumps_compact(obj: Any, sort_keys: bool = False) -> str:
    """Serialize JSON without insignificant whitespace."""
    return ujson.dumps(obj, sort_keys=sort_keys)


def canonicalize_http_method(method: str) -> str:
    """Return the uppercase HTTP method used by the signature protocol."""
    canonical = method.upper()
    if not _HTTP_METHOD_PATTERN.fullmatch(canonical):
        raise ValueError("HTTP method 格式无效")
    return canonical


def canonicalize_http_path(path: str) -> str:
    """Canonicalize a path while excluding authority, query, and fragment."""
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("签名 path 必须只包含绝对路径")
    raw_path = parsed.path
    if not raw_path.startswith("/") or _INVALID_PERCENT_ESCAPE.search(raw_path):
        raise ValueError("签名 path 格式无效")
    try:
        decoded_path = unquote(raw_path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("签名 path 不是合法 UTF-8") from exc
    if "\\" in decoded_path or "\x00" in decoded_path:
        raise ValueError("签名 path 包含非法字符")
    return quote(decoded_path, safe=_PATH_SAFE_CHARACTERS)


def request_body_sha256(body: bytes) -> str:
    """Return the SHA-256 digest of the exact HTTP request body bytes."""
    if not isinstance(body, bytes):
        raise TypeError("Worker HTTP 签名请求体必须是 bytes")
    return hashlib.sha256(body).hexdigest()


def _worker_http_signing_string(
    body: bytes,
    *,
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
) -> str:
    parts = (
        _SIGNATURE_DOMAIN,
        canonicalize_http_method(method),
        canonicalize_http_path(path),
        request_body_sha256(body),
        str(timestamp),
        nonce,
    )
    return "\n".join(parts)


def generate_hmac_signature(
    body: bytes,
    secret_key: str,
    *,
    method: str,
    path: str,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Generate a versioned signature bound to method, path, and body bytes."""
    issued_at = int(time.time()) if timestamp is None else timestamp
    request_nonce = uuid.uuid4().hex[:16] if nonce is None else nonce
    signing_string = _worker_http_signing_string(
        body,
        method=method,
        path=path,
        timestamp=issued_at,
        nonce=request_nonce,
    )
    signature = hmac.new(
        secret_key.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        WORKER_HTTP_SIGNATURE_HEADER: WORKER_HTTP_SIGNATURE_VERSION,
        "X-Signature": signature,
        "X-Timestamp": str(issued_at),
        "X-Nonce": request_nonce,
    }


def verify_hmac_signature(
    body: bytes,
    secret_key: str,
    *,
    method: str,
    path: str,
    signature: str,
    timestamp: int,
    nonce: str,
    version: str,
    max_age_seconds: int = 300,
) -> bool:
    """Verify only the current endpoint-bound Worker HTTP signature version."""
    if version != WORKER_HTTP_SIGNATURE_VERSION:
        return False
    if abs(int(time.time()) - timestamp) > max_age_seconds:
        return False
    expected = generate_hmac_signature(
        body,
        secret_key,
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
    )["X-Signature"]
    return hmac.compare_digest(signature, expected)


def compute_hmac(
    data: str | bytes,
    secret_key: str | bytes,
    algorithm: str = "sha256",
) -> str:
    """Compute a hexadecimal HMAC value."""
    encoded_data = data.encode("utf-8") if isinstance(data, str) else data
    encoded_key = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
    return hmac.new(encoded_key, encoded_data, algorithm).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    """Compare strings in constant time."""
    return hmac.compare_digest(a, b)
