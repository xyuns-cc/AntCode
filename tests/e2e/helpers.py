import asyncio
import base64
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .conftest import E2EConfig
from .source_repository import E2EGitSource

API_PREFIX = "/api/v1"
DEFAULT_TASK_TIMEOUT_SECONDS = 120
DEFAULT_RETRY_DELAY_SECONDS = 1


@dataclass(frozen=True)
class CodeProjectResources:
    project: dict[str, Any]
    repository: dict[str, Any]


def _api_path(path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{API_PREFIX}{path}"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    token: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = await client.request(method, _api_path(path), headers=headers, **kwargs)
    response.raise_for_status()
    return response.json()


def extract_data(payload: dict[str, Any]) -> Any:
    if "success" in payload:
        assert payload.get("success") is True, payload
        return payload.get("data")
    return payload


def encrypt_password(public_key_pem: str, password: str) -> str:
    public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    ciphertext = public_key.encrypt(
        password.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("ascii")


async def login(client: httpx.AsyncClient, config: E2EConfig) -> str:
    key_payload = await request_json(client, "GET", "/auth/public-key")
    key_data = extract_data(key_payload) or {}
    encrypted_password = encrypt_password(key_data["public_key"], config.admin_password)

    payload = await request_json(
        client,
        "POST",
        "/auth/login",
        json={
            "username": config.admin_user,
            "encrypted_password": encrypted_password,
            "encryption": key_data.get("algorithm"),
            "key_id": key_data.get("key_id"),
        },
    )
    data = extract_data(payload) or {}
    token = data.get("access_token")
    assert token, "登录失败: 未返回 access_token"
    return token


async def get_workers(client: httpx.AsyncClient, token: str, status: str | None = None) -> list[dict]:
    params = {"page": 1, "size": 100}
    if status:
        params["status"] = status
    payload = await request_json(client, "GET", "/workers", token=token, params=params)
    data = extract_data(payload) or {}
    return data.get("items", [])


async def get_worker(client: httpx.AsyncClient, token: str, worker_id: str | None = None) -> dict:
    max_attempts = 10
    for _ in range(max_attempts):
        online_workers = await get_workers(client, token, status="online")

        if worker_id:
            for worker in online_workers:
                if worker.get("id") == worker_id:
                    return worker

            all_workers = await get_workers(client, token, status=None)
            for worker in all_workers:
                if worker.get("id") == worker_id:
                    if worker.get("status") == "online":
                        return worker
                    break
        else:
            if online_workers:
                return online_workers[0]

        await asyncio.sleep(1)

    if worker_id:
        raise AssertionError(f"未找到在线 Worker: {worker_id}")
    raise AssertionError("未找到在线 Worker")


def assert_worker_transport_mode(worker: dict[str, Any], expected_mode: str) -> None:
    worker_id = worker.get("id")
    assert "transportMode" in worker, f"Worker 响应缺少 transportMode: worker_id={worker_id!r}"
    raw_mode = worker["transportMode"]
    assert raw_mode is not None and str(raw_mode).strip(), f"Worker 响应 transportMode 为空: worker_id={worker_id!r}"
    actual_mode = str(raw_mode).strip().lower()
    assert actual_mode == expected_mode, (
        f"Worker 传输模式不匹配: worker_id={worker_id!r}, expected={expected_mode!r}, actual={actual_mode!r}"
    )


async def ensure_shared_env(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
    *,
    config: E2EConfig,
) -> str:
    payload = await request_json(
        client,
        "GET",
        f"/runtimes/workers/{worker_id}/runtimes",
        token=token,
        params={"scope": "shared"},
    )
    envs = extract_data(payload) or []
    for env in envs:
        if env.get("name") == config.shared_env_name:
            return config.shared_env_name

    create_payload = {
        "scope": "shared",
        "python_version": config.runtime_python_version,
        "env_name": config.shared_env_name,
        "packages": [],
    }
    create_resp = await request_json(
        client,
        "POST",
        f"/runtimes/workers/{worker_id}/runtimes",
        token=token,
        json=create_payload,
    )
    data = extract_data(create_resp) or {}
    assert data.get("env"), "共享环境创建失败"
    return config.shared_env_name


async def create_code_project(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
    *,
    config: E2EConfig,
    source: E2EGitSource,
) -> CodeProjectResources:
    project_name = f"e2e-code-{uuid.uuid4().hex[:8]}"
    repository_payload = await request_json(
        client,
        "POST",
        "/repositories",
        token=token,
        json={
            "name": f"e2e-repo-{uuid.uuid4().hex[:8]}",
            "url": source.url,
            "default_ref": source.ref,
        },
    )
    repository = extract_data(repository_payload) or {}
    form = {
        "name": project_name,
        "type": "code",
        "runtime_scope": "shared",
        "python_version": config.runtime_python_version,
        "use_existing_env": "true",
        "existing_env_name": config.shared_env_name,
        "worker_id": worker_id,
        "code_entry_point": source.entry_point,
        "repository_id": repository["id"],
        "ref": source.ref,
        "subdir": source.subdir,
    }
    try:
        payload = await request_json(client, "POST", "/projects", token=token, data=form)
    except BaseException as create_error:
        try:
            await request_json(client, "DELETE", f"/repositories/{repository['id']}", token=token)
        except Exception as cleanup_error:
            raise BaseExceptionGroup(
                "E2E 项目创建与仓库清理均失败",
                [create_error, cleanup_error],
            ) from create_error
        raise
    data = extract_data(payload) or {}
    assert data.get("id"), "项目创建失败"
    return CodeProjectResources(project=data, repository=repository)


async def create_task(
    client: httpx.AsyncClient,
    token: str,
    project_id: str,
    *,
    worker_id: str,
    timeout_seconds: int = DEFAULT_TASK_TIMEOUT_SECONDS,
    retry_count: int = 0,
    retry_delay: int = DEFAULT_RETRY_DELAY_SECONDS,
) -> dict[str, Any]:
    task_name = f"e2e-task-{uuid.uuid4().hex[:8]}"
    body = {
        "name": task_name,
        "project_id": project_id,
        "schedule_type": "once",
        "is_active": True,
        "execution_strategy": "specified",
        "specified_worker_id": worker_id,
        "timeout_seconds": timeout_seconds,
        "retry_count": retry_count,
        "retry_delay": retry_delay,
    }
    payload = await request_json(client, "POST", "/tasks", token=token, json=body)
    data = extract_data(payload) or {}
    assert data.get("id"), "任务创建失败"
    return data


async def trigger_task(client: httpx.AsyncClient, token: str, task_id: str) -> None:
    payload = await request_json(client, "POST", f"/tasks/{task_id}/trigger", token=token)
    _ = extract_data(payload)


def parse_heartbeat(worker: dict[str, Any]) -> datetime | None:
    return _parse_iso(worker.get("lastHeartbeat"))
