#!/usr/bin/env python3
import http.client
import json
import os
import sys
from urllib.parse import SplitResult, urlsplit

HEALTHCHECK_TIMEOUT_SECONDS = 5


def _health_url() -> str:
    port = os.getenv("SERVER_PORT", "8000")
    base_url = os.getenv("HEALTHCHECK_BASE_URL", f"http://127.0.0.1:{port}").rstrip("/")
    _parse_http_url(base_url)
    return f"{base_url}/api/v1/health"


def _parse_http_url(url: str) -> SplitResult:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HEALTHCHECK_BASE_URL 必须是有效的 HTTP(S) 地址")
    return parsed


def _connection(url: str) -> tuple[http.client.HTTPConnection, str]:
    parsed = _parse_http_url(url)
    host = parsed.hostname
    if host is None:
        raise ValueError("HEALTHCHECK_BASE_URL 缺少主机名")
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(host, parsed.port, timeout=HEALTHCHECK_TIMEOUT_SECONDS)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    return connection, target


def _check_health(url: str) -> None:
    connection, target = _connection(url)
    try:
        connection.request("GET", target)
        response = connection.getresponse()
        if response.getcode() != 200:
            raise RuntimeError(f"health failed: http {response.getcode()}")
        payload = json.loads(response.read())
    finally:
        connection.close()
    if not isinstance(payload, dict):
        raise RuntimeError("health failed: response is not an object")
    data = payload.get("data")
    if payload.get("success") is not True or not isinstance(data, dict) or data.get("status") != "healthy":
        raise RuntimeError("health failed: response is not healthy")


def main() -> int:
    try:
        _check_health(_health_url())
    except Exception as exc:
        sys.stderr.write(f"health error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
