"""
E2E 测试配置和 fixtures

提供跨服务测试所需的基础设施配置。
"""

import asyncio
import os
from collections.abc import Generator
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import httpx
import pytest
import pytest_asyncio

E2E_CONFIRM_ENV = "ANTCODE_E2E_CONFIRM"
E2E_FULL_CONFIRMATION = "FULL"
E2E_DATABASE_NAME = "antcode_e2e_test"
E2E_DATABASE_HOST_ENV = "ANTCODE_E2E_DATABASE_HOST"
E2E_DATABASE_NAME_ENV = "ANTCODE_E2E_DATABASE_NAME"
E2E_TRANSPORT_MODE_ENV = "ANTCODE_E2E_EXPECT_TRANSPORT_MODE"
E2E_TRANSPORT_MODES = frozenset({"direct", "gateway"})
E2E_WORKER_ID_ENV = "ANTCODE_E2E_WORKER_ID"


@dataclass(frozen=True)
class E2EConfig:
    web_api_url: str
    admin_user: str
    admin_password: str
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


def _database_identity(database_url: str) -> tuple[str, str]:
    parsed = urlsplit(database_url)
    if parsed.scheme.lower() not in {"postgres", "postgresql"}:
        raise ValueError("E2E DATABASE_URL 必须使用 PostgreSQL")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("E2E DATABASE_URL 必须包含数据库 host")
    database = unquote(parsed.path.lstrip("/")).strip().lower()
    return host, database


def require_e2e_authorization() -> str:
    confirmation = (_env(E2E_CONFIRM_ENV) or "").strip().upper()
    if confirmation != E2E_FULL_CONFIRMATION:
        raise RuntimeError(f"运行 E2E 必须显式设置 {E2E_CONFIRM_ENV}={E2E_FULL_CONFIRMATION}")
    require_e2e_worker_id()
    database_url = _required_env("DATABASE_URL")
    expected_host = _required_env(E2E_DATABASE_HOST_ENV).lower()
    expected_database = _required_env(E2E_DATABASE_NAME_ENV).lower()
    if expected_database != E2E_DATABASE_NAME:
        raise ValueError(f"{E2E_DATABASE_NAME_ENV} 必须绑定专用数据库 {E2E_DATABASE_NAME!r}")
    actual_host, actual_database = _database_identity(database_url)
    if actual_host != expected_host:
        raise ValueError(f"E2E 数据库 host 绑定不匹配: expected={expected_host!r}, actual={actual_host!r}")
    if actual_database != expected_database:
        raise ValueError(f"E2E 数据库名称绑定不匹配: expected={expected_database!r}, actual={actual_database!r}")
    return database_url


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
    web_api_url = _env("ANTCODE_E2E_WEB_API_URL", "http://127.0.0.1:8000")
    runtime_python_version = _env("ANTCODE_E2E_RUNTIME_PYTHON", "3.12")
    shared_env_name = _env(
        "ANTCODE_E2E_SHARED_ENV",
        f"shared-py{runtime_python_version.replace('.', '')}",
    )

    return E2EConfig(
        web_api_url=web_api_url,
        admin_user=_env("ANTCODE_E2E_ADMIN_USER", "admin"),
        admin_password=_env("ANTCODE_E2E_ADMIN_PASSWORD", "Admin123!"),
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


@pytest_asyncio.fixture(scope="session", autouse=True)
async def ensure_e2e_admin_credentials(e2e_config: E2EConfig) -> None:
    """确保 E2E 使用的管理员账号可用且口令一致。"""
    require_e2e_authorization()

    from antcode_core.domain.models.user import User, UserRole
    from antcode_core.infrastructure.db.tortoise import get_default_tortoise_config
    from tortoise import Tortoise

    await Tortoise.init(config=get_default_tortoise_config())
    try:
        admin_user = await User.filter(username=e2e_config.admin_user).first()
        if admin_user is None:
            admin_user = User(
                username=e2e_config.admin_user,
                email="admin@example.com",
                is_active=True,
                role=UserRole.SUPER_ADMIN,
            )
            admin_user.set_password(e2e_config.admin_password)
            await admin_user.save()
            return

        update_fields: list[str] = []
        if not admin_user.verify_password(e2e_config.admin_password):
            admin_user.set_password(e2e_config.admin_password)
            update_fields.append("password_hash")
        if admin_user.role != UserRole.SUPER_ADMIN or not admin_user.is_admin:
            admin_user.role = UserRole.SUPER_ADMIN
            admin_user.is_admin = True
            update_fields.extend(["role", "is_admin"])
        if not admin_user.is_active:
            admin_user.is_active = True
            update_fields.append("is_active")
        if update_fields:
            await admin_user.save(update_fields=list(dict.fromkeys(update_fields)))
    finally:
        await Tortoise.close_connections()


@pytest_asyncio.fixture(scope="session")
async def e2e_token(
    e2e_config: E2EConfig,
    ensure_e2e_admin_credentials: None,
) -> str:
    """Login once per session so E2E respects the production auth limiter."""
    from .helpers import login

    async with httpx.AsyncClient(
        base_url=e2e_config.web_api_url,
        timeout=e2e_config.http_timeout,
    ) as client:
        return await login(client, e2e_config)


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
