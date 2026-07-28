from types import SimpleNamespace

from antcode_web_api.middleware.middleware import AdminPermissionMiddleware, jwt_auth
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(AdminPermissionMiddleware)

    @app.get("/api/v1/users")
    async def users() -> dict[str, bool]:
        return {"ok": True}

    @app.put("/api/v1/users/{user_id}")
    async def update_user(user_id: str) -> dict[str, str]:
        return {"user_id": user_id}

    return TestClient(app)


def test_admin_middleware_returns_401_without_bearer_token() -> None:
    with _client() as client:
        response = client.get("/api/v1/users")

    assert response.status_code == 401
    assert response.json()["message"] == "缺少认证信息"


def test_admin_middleware_returns_403_for_regular_user(monkeypatch) -> None:
    monkeypatch.setattr(jwt_auth, "verify_token", lambda token: SimpleNamespace(is_admin=False))

    with _client() as client:
        response = client.get(
            "/api/v1/users",
            headers={"Authorization": "Bearer regular-user-token"},
        )

    assert response.status_code == 403
    assert response.json()["message"] == "需要管理员权限"


def test_admin_middleware_allows_admin(monkeypatch) -> None:
    monkeypatch.setattr(jwt_auth, "verify_token", lambda token: SimpleNamespace(is_admin=True))

    with _client() as client:
        response = client.get(
            "/api/v1/users",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_admin_middleware_defers_self_update_acl_to_route(monkeypatch) -> None:
    monkeypatch.setattr(jwt_auth, "verify_token", lambda token: SimpleNamespace(is_admin=False))

    with _client() as client:
        response = client.put(
            "/api/v1/users/user-public-id",
            headers={"Authorization": "Bearer regular-user-token"},
        )

    assert response.status_code == 200
    assert response.json() == {"user_id": "user-public-id"}
