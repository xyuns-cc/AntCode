"""真实用户管理、RBAC 与会话撤销端到端测试。"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest

from .conftest import E2EConfig, requires_postgres, requires_redis
from .helpers import API_PREFIX, encrypt_password, extract_data, request_json

EXPECTED_USER_PERMISSIONS = {
    "project:read",
    "task:read",
    "user:read",
    "worker:read",
}
REFRESH_COOKIE = "antcode_refresh"


@dataclass(frozen=True)
class ProvisionedUser:
    public_id: str
    username: str
    password: str


def _client(config: E2EConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=config.web_api_url, timeout=config.http_timeout)


async def _login(client: httpx.AsyncClient, user: ProvisionedUser) -> str:
    key_payload = await request_json(client, "GET", "/auth/public-key")
    key_data = extract_data(key_payload) or {}
    payload = await request_json(
        client,
        "POST",
        "/auth/login",
        json={
            "username": user.username,
            "encrypted_password": encrypt_password(key_data["public_key"], user.password),
            "encryption": key_data.get("algorithm"),
            "key_id": key_data.get("key_id"),
        },
    )
    data = extract_data(payload) or {}
    assert data["user"]["role"] == "user"
    assert data["user"]["is_admin"] is False
    assert client.cookies.get(REFRESH_COOKIE)
    return data["access_token"]


async def _delete_user(client: httpx.AsyncClient, admin_token: str, public_id: str) -> None:
    await request_json(client, "DELETE", f"/users/{public_id}", token=admin_token)
    response = await client.get(
        f"{API_PREFIX}/users/{public_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404, response.text


@asynccontextmanager
async def _provision_user(
    client: httpx.AsyncClient,
    admin_token: str,
) -> AsyncIterator[ProvisionedUser]:
    suffix = uuid.uuid4().hex[:12]
    user = ProvisionedUser(
        public_id="",
        username=f"e2e-user-{suffix}",
        password=f"E2e!{suffix}Aa1",
    )
    # 建号的初始口令与登录同一类机密，走同一把 /auth/public-key 公钥；
    # 服务端默认 LOGIN_PASSWORD_ENCRYPTION_REQUIRED=true，发明文会 400。
    key_data = extract_data(await request_json(client, "GET", "/auth/public-key")) or {}
    payload = await request_json(
        client,
        "POST",
        "/users/",
        token=admin_token,
        json={
            "username": user.username,
            "encrypted_password": encrypt_password(key_data["public_key"], user.password),
            "encryption": key_data.get("algorithm"),
            "key_id": key_data.get("key_id"),
            "role": "user",
        },
    )
    created = extract_data(payload) or {}
    user = ProvisionedUser(created["id"], user.username, user.password)
    try:
        assert created["role"] == "user"
        assert created["is_admin"] is False
        yield user
    except BaseException as scenario_error:
        try:
            await _delete_user(client, admin_token, user.public_id)
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "用户安全 E2E 场景与清理均失败",
                [scenario_error, cleanup_error],
            ) from scenario_error
        raise
    else:
        await _delete_user(client, admin_token, user.public_id)


async def _assert_session_rejected(
    client: httpx.AsyncClient,
    access_token: str,
    refresh_token: str,
) -> None:
    me_response = await client.get(
        f"{API_PREFIX}/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_response.status_code == 401, me_response.text
    async with httpx.AsyncClient(base_url=client.base_url, timeout=client.timeout) as replay_client:
        refresh_response = await replay_client.post(
            f"{API_PREFIX}/auth/refresh",
            json={"refresh_token": refresh_token},
        )
    assert refresh_response.status_code == 401, refresh_response.text


async def _assert_rbac(client: httpx.AsyncClient, user: ProvisionedUser) -> None:
    token = await _login(client, user)
    permissions = extract_data(await request_json(client, "GET", "/auth/permissions", token=token))
    assert permissions["permissions"] == sorted(EXPECTED_USER_PERMISSIONS)

    headers = {"Authorization": f"Bearer {token}"}
    users_response = await client.get(f"{API_PREFIX}/users/", headers=headers)
    kick_response = await client.post(
        f"{API_PREFIX}/users/{user.public_id}/kick",
        headers=headers,
    )
    assert users_response.status_code == 403, users_response.text
    assert kick_response.status_code == 403, kick_response.text

    escalation = await client.put(
        f"{API_PREFIX}/users/{user.public_id}",
        headers=headers,
        json={"role": "super_admin", "is_admin": True},
    )
    # 校验失败统一 422：这是 validation_exception_handler 的全局约定，且被
    # test_exception_responses.py::test_validation_exception_handler_matches_openapi_422_contract
    # 锁成 OpenAPI 契约。本用例真正要断言的安全性质不变——提权被拒，且精确
    # 报出 role / is_admin 两个越权字段。
    assert escalation.status_code == 422, escalation.text
    errors = escalation.json()["data"]["errors"]
    assert {error["field"] for error in errors} == {"body.role", "body.is_admin"}
    me = extract_data(await request_json(client, "GET", "/auth/me", token=token))
    assert me["role"] == "user"
    assert me["is_admin"] is False


async def _assert_refresh_and_logout(config: E2EConfig, user: ProvisionedUser) -> None:
    async with _client(config) as client:
        first_access = await _login(client, user)
        first_refresh = client.cookies[REFRESH_COOKIE]
        refreshed = extract_data(await request_json(client, "POST", "/auth/refresh"))
        rotated_access = refreshed["access_token"]
        rotated_refresh = client.cookies[REFRESH_COOKIE]
        assert rotated_access != first_access
        assert rotated_refresh != first_refresh

        await _assert_session_rejected(client, first_access, first_refresh)
        await request_json(client, "POST", "/auth/logout", token=rotated_access)
        assert client.cookies.get(REFRESH_COOKIE) is None
        await _assert_session_rejected(client, rotated_access, rotated_refresh)


async def _assert_admin_kick(
    admin_client: httpx.AsyncClient,
    admin_token: str,
    user: ProvisionedUser,
    *,
    config: E2EConfig,
) -> None:
    async with _client(config) as client_a, _client(config) as client_b:
        access_a = await _login(client_a, user)
        access_b = await _login(client_b, user)
        refresh_a = client_a.cookies[REFRESH_COOKIE]
        refresh_b = client_b.cookies[REFRESH_COOKIE]
        kicked = extract_data(
            await request_json(
                admin_client,
                "POST",
                f"/users/{user.public_id}/kick",
                token=admin_token,
            )
        )
        assert kicked["revoked_sessions"] == 2
        await _assert_session_rejected(client_a, access_a, refresh_a)
        await _assert_session_rejected(client_b, access_b, refresh_b)


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_regular_user_rbac_and_exact_permissions(e2e_config, e2e_token) -> None:
    async with _client(e2e_config) as admin_client:
        async with _provision_user(admin_client, e2e_token) as user:
            async with _client(e2e_config) as user_client:
                await _assert_rbac(user_client, user)


@requires_postgres
@requires_redis
@pytest.mark.asyncio
async def test_refresh_logout_and_admin_kick_revoke_sessions(e2e_config, e2e_token) -> None:
    async with _client(e2e_config) as admin_client:
        async with _provision_user(admin_client, e2e_token) as user:
            await _assert_refresh_and_logout(e2e_config, user)
            await _assert_admin_kick(
                admin_client,
                e2e_token,
                user,
                config=e2e_config,
            )
