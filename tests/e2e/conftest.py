"""
E2E 测试配置和 fixtures

提供跨服务测试所需的基础设施配置。
"""

import asyncio
import os
from collections.abc import Generator
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio

E2E_CONFIRM_ENV = "ANTCODE_E2E_CONFIRM"
E2E_FULL_CONFIRMATION = "FULL"
E2E_WEB_API_URL_ENV = "ANTCODE_E2E_WEB_API_URL"
E2E_ADMIN_USER_ENV = "ANTCODE_E2E_ADMIN_USER"
E2E_ADMIN_PASSWORD_ENV = "ANTCODE_E2E_ADMIN_PASSWORD"
E2E_TRANSPORT_MODE_ENV = "ANTCODE_E2E_EXPECT_TRANSPORT_MODE"
E2E_TRANSPORT_MODES = frozenset({"direct", "gateway"})
E2E_WORKER_ID_ENV = "ANTCODE_E2E_WORKER_ID"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class SensitiveString(str):
    """保持字符串行为，但禁止 pytest/对象 repr 暴露敏感值。"""

    def __repr__(self) -> str:
        return "<redacted>"


@dataclass(frozen=True)
class E2EConfig:
    web_api_url: str
    admin_user: str
    admin_password: str = field(repr=False)
    worker_id: str
    runtime_python_version: str
    shared_env_name: str
    poll_interval: float
    poll_timeout: float
    http_timeout: float
    expected_transport_mode: str


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _required_env(name: str) -> str:
    value = (_env(name) or "").strip()
    if not value:
        raise RuntimeError(f"运行 E2E 必须显式设置 {name}")
    return value


def _validate_e2e_web_api_url(value: str) -> str:
    parsed = urlsplit(value)
    is_https = parsed.scheme.lower() == "https"
    is_loopback_http = parsed.scheme.lower() == "http" and parsed.hostname in LOOPBACK_HOSTS
    if not parsed.hostname or not (is_https or is_loopback_http):
        raise ValueError(f"{E2E_WEB_API_URL_ENV} 必须使用 HTTPS；仅本机回环 CI 允许显式 HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{E2E_WEB_API_URL_ENV} 不允许包含凭证、query 或 fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError(f"{E2E_WEB_API_URL_ENV} 只能配置 HTTPS origin，不能包含路径")
    return value.rstrip("/")


def require_e2e_authorization() -> str:
    confirmation = (_env(E2E_CONFIRM_ENV) or "").strip().upper()
    if confirmation != E2E_FULL_CONFIRMATION:
        raise RuntimeError(f"运行 E2E 必须显式设置 {E2E_CONFIRM_ENV}={E2E_FULL_CONFIRMATION}")
    require_e2e_worker_id()
    return _validate_e2e_web_api_url(_required_env(E2E_WEB_API_URL_ENV))


def require_e2e_transport_mode() -> str:
    mode = (_env(E2E_TRANSPORT_MODE_ENV) or "").strip().lower()
    if mode not in E2E_TRANSPORT_MODES:
        allowed = ", ".join(sorted(E2E_TRANSPORT_MODES))
        raise RuntimeError(f"运行 E2E 必须显式设置 {E2E_TRANSPORT_MODE_ENV}，允许值: {allowed}")
    return mode


def require_e2e_worker_id() -> str:
    worker_id = (_env(E2E_WORKER_ID_ENV) or "").strip()
    if not worker_id:
        raise RuntimeError(f"FULL E2E 必须显式设置专用 Worker: {E2E_WORKER_ID_ENV}")
    return worker_id


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """创建事件循环，用于整个测试会话"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def e2e_config() -> E2EConfig:
    web_api_url = require_e2e_authorization()
    runtime_python_version = _env("ANTCODE_E2E_RUNTIME_PYTHON", "3.12")
    shared_env_name = _env(
        "ANTCODE_E2E_SHARED_ENV",
        f"shared-py{runtime_python_version.replace('.', '')}",
    )

    return E2EConfig(
        web_api_url=web_api_url,
        admin_user=_required_env(E2E_ADMIN_USER_ENV),
        admin_password=_required_env(E2E_ADMIN_PASSWORD_ENV),
        worker_id=require_e2e_worker_id(),
        runtime_python_version=runtime_python_version,
        shared_env_name=shared_env_name,
        poll_interval=float(_env("ANTCODE_E2E_POLL_INTERVAL", "2")),
        poll_timeout=float(_env("ANTCODE_E2E_POLL_TIMEOUT", "180")),
        http_timeout=float(_env("ANTCODE_E2E_HTTP_TIMEOUT", "30")),
        expected_transport_mode=require_e2e_transport_mode(),
    )


requires_postgres = pytest.mark.e2e
requires_redis = pytest.mark.e2e


@pytest_asyncio.fixture(scope="session")
async def e2e_token(e2e_config: E2EConfig) -> SensitiveString:
    """只通过 HTTPS 登录预先 bootstrap 的管理员，不直接修改数据库。"""
    from .helpers import login

    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        return SensitiveString(await login(client, e2e_config))


@pytest_asyncio.fixture(scope="session", autouse=True)
async def ensure_e2e_worker_transport_mode(e2e_config: E2EConfig, e2e_token: str) -> None:
    """在任何业务场景开始前确认 Worker 实际运行于目标传输模式。"""
    from .helpers import assert_worker_transport_mode, get_worker

    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        worker = await get_worker(client, e2e_token, e2e_config.worker_id)
    assert_worker_transport_mode(worker, e2e_config.expected_transport_mode)
