#!/usr/bin/env python3
import argparse
import asyncio
import base64
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from loguru import logger


API_PREFIX = "/api/v1"
TERMINAL_STATUSES = {"success", "failed", "cancelled", "timeout"}


@dataclass(frozen=True)
class ChainConfig:
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


def build_config() -> ChainConfig:
    web_api_url = _env("ANTCODE_E2E_WEB_API_URL", "http://127.0.0.1:8000")
    ws_url = _env("ANTCODE_E2E_WS_URL", _derive_ws_url(web_api_url))
    python_version = _env("ANTCODE_E2E_PYTHON_VERSION", "3.12")
    shared_env_name = _env(
        "ANTCODE_E2E_SHARED_ENV",
        f"shared-py{python_version.replace('.', '')}",
    )
    return ChainConfig(
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


def _api_path(path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{API_PREFIX}{path}"


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
        if payload.get("success") is not True:
            raise RuntimeError(f"接口返回失败: {payload}")
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


async def login(client: httpx.AsyncClient, config: ChainConfig) -> str:
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
    if not token:
        raise RuntimeError("登录失败: 未返回 access_token")
    return token


async def get_worker(client: httpx.AsyncClient, token: str, worker_id: str | None) -> dict[str, Any]:
    payload = await request_json(
        client,
        "GET",
        "/workers",
        token=token,
        params={"page": 1, "size": 100, "status": "online"},
    )
    data = extract_data(payload) or {}
    items = data.get("items", [])
    if worker_id:
        for item in items:
            if item.get("id") == worker_id:
                return item
        raise RuntimeError(f"未找到在线 Worker: {worker_id}")
    if not items:
        raise RuntimeError("未找到在线 Worker")
    return items[0]


async def ensure_shared_env(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
    config: ChainConfig,
) -> str:
    payload = await request_json(
        client,
        "GET",
        f"/runtimes/workers/{worker_id}/envs",
        token=token,
        params={"scope": "shared"},
    )
    envs = extract_data(payload) or []
    for env in envs:
        if env.get("name") == config.shared_env_name:
            return config.shared_env_name

    create_payload = {
        "scope": "shared",
        "python_version": config.python_version,
        "env_name": config.shared_env_name,
        "packages": [],
    }
    create_resp = await request_json(
        client,
        "POST",
        f"/runtimes/workers/{worker_id}/envs",
        token=token,
        json=create_payload,
    )
    data = extract_data(create_resp) or {}
    if not data.get("env"):
        raise RuntimeError("共享环境创建失败")
    return config.shared_env_name


async def create_code_project(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
    config: ChainConfig,
    log_token: str,
) -> dict[str, Any]:
    project_name = f"e2e-code-{uuid.uuid4().hex[:8]}"
    code_content = (
        "import logging\n"
        "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "logger = logging.getLogger(__name__)\n"
        f"logger.info('{log_token}')\n"
    )
    form = {
        "name": project_name,
        "type": "code",
        "runtime_scope": "shared",
        "python_version": config.python_version,
        "use_existing_env": "true",
        "existing_env_name": config.shared_env_name,
        "worker_id": worker_id,
        "code_entry_point": "main.py",
        "code_content": code_content,
    }
    payload = await request_json(client, "POST", "/projects", token=token, data=form)
    data = extract_data(payload) or {}
    if not data.get("id"):
        raise RuntimeError("项目创建失败")
    return data


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
    if not data.get("id"):
        raise RuntimeError("任务创建失败")
    return data


async def trigger_task(client: httpx.AsyncClient, token: str, task_id: str) -> None:
    payload = await request_json(client, "POST", f"/tasks/{task_id}/trigger", token=token)
    _ = extract_data(payload)


async def wait_for_execution(
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
        items = payload.get("items", [])
        if items:
            run = items[0]
            status = run.get("status")
            last_status = status
            if status in TERMINAL_STATUSES:
                if status != "success":
                    raise RuntimeError(f"任务执行失败: {status}, run={run}")
                return run
        await asyncio.sleep(interval)
    raise RuntimeError(f"等待任务完成超时: last_status={last_status}")


async def get_logs(
    client: httpx.AsyncClient,
    token: str,
    execution_id: str,
    log_format: str,
) -> dict[str, Any]:
    payload = await request_json(
        client,
        "GET",
        f"/logs/executions/{execution_id}",
        token=token,
        params={"format": log_format},
    )
    return extract_data(payload) or {}


async def check_websocket(
    config: ChainConfig,
    execution_id: str,
    token: str,
) -> dict[str, Any]:
    import websockets

    url = f"{config.ws_url}{API_PREFIX}/ws/executions/{execution_id}/logs?token={token}"
    async with websockets.connect(url, ping_interval=None) as websocket:
        message = await asyncio.wait_for(websocket.recv(), timeout=15)
        return json.loads(message)


async def run_full_chain(config: ChainConfig, with_ws: bool) -> None:
    async with httpx.AsyncClient(
        base_url=config.web_api_url, timeout=config.http_timeout
    ) as client:
        token = await login(client, config)
        logger.info("[OK] login")

        worker = await get_worker(client, token, config.worker_id)
        logger.info("[OK] worker online: {} {}", worker.get("id"), worker.get("name"))

        await ensure_shared_env(client, token, worker["id"], config)
        logger.info("[OK] shared env ready: {}", config.shared_env_name)

        log_token = f"E2E-OK-{uuid.uuid4().hex[:8]}"
        project = await create_code_project(client, token, worker["id"], config, log_token)
        logger.info("[OK] project created: {}", project.get("id"))

        task = await create_task(client, token, project["id"], worker["id"])
        logger.info("[OK] task created: {}", task.get("id"))

        await trigger_task(client, token, task["id"])
        logger.info("[OK] task triggered")

        execution = await wait_for_execution(
            client,
            token,
            task["id"],
            timeout=config.poll_timeout,
            interval=config.poll_interval,
        )
        execution_id = execution.get("execution_id")
        logger.info("[OK] execution success: {}", execution_id)

        raw_logs = await get_logs(client, token, execution_id, log_format="raw")
        raw_content = raw_logs.get("raw_content", "")
        if log_token not in raw_content:
            raise RuntimeError("raw 日志未包含期望输出")
        logger.info("[OK] raw logs")

        structured_logs = await get_logs(client, token, execution_id, log_format="structured")
        items = (structured_logs.get("structured_data") or {}).get("items", [])
        if not items:
            raise RuntimeError("structured 日志为空")
        logger.info("[OK] structured logs")

        if with_ws:
            message = await check_websocket(config, execution_id, token)
            msg_type = message.get("type")
            logger.info("[OK] websocket message: {}", msg_type)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AntCode 全链路验证脚本")
    parser.add_argument("--ws", action="store_true", help="同时验证 WebSocket 日志")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = build_config()
    try:
        asyncio.run(run_full_chain(config, with_ws=args.ws))
    except Exception as exc:
        logger.error("[FAIL] {}", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
