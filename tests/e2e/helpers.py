import asyncio
import base64
import json
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .conftest import E2EConfig

API_PREFIX = "/api/v1"
TERMINAL_STATUSES = {"success", "failed", "cancelled", "timeout"}
_E2E_REPOS: list[TemporaryDirectory[str]] = []


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


async def ensure_shared_env(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
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
    config: E2EConfig,
    log_token: str,
) -> dict[str, Any]:
    project_name = f"e2e-code-{uuid.uuid4().hex[:8]}"
    repository_url = create_git_repo_with_main(log_token)
    repository_payload = await request_json(
        client,
        "POST",
        "/repositories",
        token=token,
        json={
            "name": f"e2e-repo-{uuid.uuid4().hex[:8]}",
            "url": repository_url,
            "default_ref": "main",
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
        "code_entry_point": "main.py",
        "repository_id": repository["id"],
        "ref": "main",
        "subdir": "spiders/e2e",
    }
    payload = await request_json(client, "POST", "/projects", token=token, data=form)
    data = extract_data(payload) or {}
    assert data.get("id"), "项目创建失败"
    return data


def create_git_repo_with_main(log_token: str) -> str:
    temp_dir = TemporaryDirectory()
    _E2E_REPOS.append(temp_dir)
    repo = Path(temp_dir.name)
    source_dir = repo / "spiders" / "e2e"
    source_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text(_main_py(log_token), encoding="utf-8")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "e2e@example.com")
    _git(repo, "config", "user.name", "AntCode E2E")
    _git(repo, "add", "spiders/e2e/main.py")
    _git(repo, "commit", "-m", "init")
    return repo.as_uri()


def _main_py(log_token: str) -> str:
    return (
        "import logging\n"
        "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "logger = logging.getLogger(__name__)\n"
        f"logger.info('{log_token}')\n"
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


async def create_task(
    client: httpx.AsyncClient,
    token: str,
    project_id: str,
    worker_id: str,
) -> dict[str, Any]:
    task_name = f"e2e-task-{uuid.uuid4().hex[:8]}"
    body = {
        "name": task_name,
        "project_id": project_id,
        "schedule_type": "once",
        "is_active": True,
        "execution_strategy": "specified",
        "specified_worker_id": worker_id,
        "timeout_seconds": 120,
        "retry_count": 0,
    }
    payload = await request_json(client, "POST", "/tasks", token=token, json=body)
    data = extract_data(payload) or {}
    assert data.get("id"), "任务创建失败"
    return data


async def trigger_task(client: httpx.AsyncClient, token: str, task_id: str) -> None:
    payload = await request_json(client, "POST", f"/tasks/{task_id}/trigger", token=token)
    _ = extract_data(payload)


async def wait_for_run(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
    timeout: float,
    interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        payload = await request_json(
            client,
            "GET",
            f"/tasks/{task_id}/runs",
            token=token,
            params={"page": 1, "size": 5},
        )
        data = extract_data(payload)
        if isinstance(data, dict):
            items = data.get("items", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []
        if items:
            run = items[0]
            status = run.get("status")
            last_status = status
            if status in TERMINAL_STATUSES:
                if status != "success":
                    raise AssertionError(f"任务执行失败: {status}, run={run}")
                return run
        await asyncio.sleep(interval)
    raise AssertionError(f"等待任务完成超时: last_status={last_status}")


async def create_run_context(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
    config: E2EConfig,
) -> dict[str, Any]:
    log_token = f"E2E-OK-{uuid.uuid4().hex[:8]}"
    await ensure_shared_env(client, token, worker_id, config)
    project = await create_code_project(client, token, worker_id, config, log_token)
    task = await create_task(client, token, project["id"], worker_id)
    await trigger_task(client, token, task["id"])
    run = await wait_for_run(
        client,
        token,
        task["id"],
        timeout=config.poll_timeout,
        interval=config.poll_interval,
    )
    return {
        "project": project,
        "task": task,
        "run": run,
        "log_token": log_token,
    }


async def get_logs(
    client: httpx.AsyncClient,
    token: str,
    run_id: str,
    log_format: str,
) -> dict[str, Any]:
    payload = await request_json(
        client,
        "GET",
        f"/logs/runs/{run_id}",
        token=token,
        params={"format": log_format},
    )
    return extract_data(payload) or {}


async def wait_for_websocket_message(
    config: E2EConfig,
    run_id: str,
    token: str,
    timeout: float = 15.0,
) -> dict[str, Any]:
    url = f"{config.ws_url}{API_PREFIX}/ws/runs/{run_id}/logs?token={token}"
    async with websockets.connect(url, ping_interval=None) as websocket:
        message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
        data = json.loads(message)
        return data


def parse_heartbeat(worker: dict[str, Any]) -> datetime | None:
    return _parse_iso(worker.get("lastHeartbeat"))
