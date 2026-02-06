"""
E2E 测试配置和 fixtures

提供跨服务测试所需的基础设施配置。
"""

import asyncio
import os
from collections.abc import Generator
from dataclasses import dataclass
from urllib.parse import urlparse

import pytest


@dataclass(frozen=True)
class E2EConfig:
    web_api_url: str
    ws_url: str
    admin_user: str
    admin_password: str
    worker_id: str | None
    python_version: str
    shared_env_name: str
    poll_interval: float
    poll_timeout: float
    http_timeout: float


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _derive_ws_url(http_url: str) -> str:
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc or parsed.path
    return f"{scheme}://{netloc}"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """创建事件循环，用于整个测试会话"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def e2e_config() -> E2EConfig:
    web_api_url = _env("ANTCODE_E2E_WEB_API_URL", "http://127.0.0.1:8000")
    ws_url = _env("ANTCODE_E2E_WS_URL", _derive_ws_url(web_api_url))
    python_version = _env("ANTCODE_E2E_PYTHON_VERSION", "3.12")
    shared_env_name = _env(
        "ANTCODE_E2E_SHARED_ENV",
        f"shared-py{python_version.replace('.', '')}",
    )

    return E2EConfig(
        web_api_url=web_api_url,
        ws_url=ws_url,
        admin_user=_env("ANTCODE_E2E_ADMIN_USER", "admin"),
        admin_password=_env("ANTCODE_E2E_ADMIN_PASSWORD", "Admin123!"),
        worker_id=_env("ANTCODE_E2E_WORKER_ID"),
        python_version=python_version,
        shared_env_name=shared_env_name,
        poll_interval=float(_env("ANTCODE_E2E_POLL_INTERVAL", "2")),
        poll_timeout=float(_env("ANTCODE_E2E_POLL_TIMEOUT", "180")),
        http_timeout=float(_env("ANTCODE_E2E_HTTP_TIMEOUT", "30")),
    )


_SKIP_E2E = _env("ANTCODE_E2E_SKIP", "0") == "1"

# 标记需要基础设施的测试（默认不跳过）
requires_mysql = pytest.mark.skipif(_SKIP_E2E, reason="E2E 已通过环境变量跳过")
requires_redis = pytest.mark.skipif(_SKIP_E2E, reason="E2E 已通过环境变量跳过")
requires_minio = pytest.mark.skipif(_SKIP_E2E, reason="E2E 已通过环境变量跳过")
