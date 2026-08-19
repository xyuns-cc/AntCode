"""发布 E2E 的 Worker 机群引导：注册 -> 按 worker_id 重签客户端证书 -> mTLS 接入。

每个 Worker 的身份来源是**它自己容器里持久化的 worker_credentials.json**，而不是
"控制台上新增的那一行"。后者在多 Worker 并发注册时无法把行与容器对上（曾经的
``_wait_for_new_worker`` 因此硬性只允许一个新 Worker）；前者是权威且天然一一对应的。
控制台侧仍然核对一遍：新增 Worker 集合必须与各容器自报的身份完全相等，避免
"多注册出一个没人认领的 Worker" 被放过。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from scripts.release_e2e_compose import run as compose_run
from scripts.release_e2e_compose import worker as worker_compose
from scripts.release_e2e_environment import worker_variables
from scripts.release_e2e_pki import write_worker_identity_certificate
from tests.e2e.helpers import extract_data, get_workers, request_json

WORKER_REGISTRATION_TIMEOUT_SECONDS = 180
WORKER_REGISTRATION_POLL_SECONDS = 3
WORKER_START_TIMEOUT_SECONDS = 300
INSTALL_KEY_FILE_MODE = 0o600
#: 容器内自报身份：既做持久化断言，又把 worker_id 打到 stdout 供编排器读取。
WORKER_CREDENTIAL_PROBE = """
import json
from pathlib import Path

directory = Path("/app/data/worker/secrets")
credentials = json.loads((directory / "worker_credentials.json").read_text(encoding="utf-8"))
required = ("worker_id", "api_key", "secret_key", "registration_id")
assert all(credentials.get(name) for name in required)
assert not (directory / "worker_registration_intent.json").exists()
print(credentials["worker_id"])
"""


@dataclass(frozen=True)
class Fleet:
    """一轮发布 E2E 里全部 Worker 的编排入参。"""

    environment: Path
    state_dir: Path
    slug: str
    count: int

    def indexes(self) -> range:
        return range(self.count)

    def variables(self, index: int) -> dict[str, str]:
        return worker_variables(self.state_dir, self.slug, index)

    def compose(self, index: int, *, bootstrap: bool = False) -> list[str]:
        return worker_compose(self.environment, index, bootstrap=bootstrap)


async def create_install_keys(client: httpx.AsyncClient, token: str, count: int) -> list[str]:
    """安装 Key 是一次性凭据（注册时 status pending -> used），每个 Worker 各签一把。"""
    keys: list[str] = []
    for _ in range(count):
        payload = await request_json(
            client,
            "POST",
            "/workers/generate-install-key",
            token=token,
            json={"os_type": "linux"},
        )
        key = str((extract_data(payload) or {})["key"])
        print(f"::add-mask::{key}")
        keys.append(key)
    return keys


def bootstrap(fleet: Fleet, install_keys: list[str]) -> None:
    """把安装 Key 落盘并同时拉起全部 Worker——并发注册本身就是要验的场景。"""
    for index, install_key in zip(fleet.indexes(), install_keys, strict=True):
        variables = fleet.variables(index)
        key_file = Path(variables["ANTCODE_WORKER_INSTALL_KEY_FILE"])
        key_file.write_text(install_key, encoding="utf-8")
        key_file.chmod(INSTALL_KEY_FILE_MODE)
        compose_run([*fleet.compose(index, bootstrap=True), "up", "-d", "worker"], env=variables)


def collect_identities(fleet: Fleet) -> list[str]:
    """逐个容器读取它自己持久化的 worker_id；这是容器与身份的权威映射。"""
    identities = [_await_identity(fleet, index) for index in fleet.indexes()]
    if len(set(identities)) != fleet.count:
        raise RuntimeError(f"release E2E Workers reported duplicate identities: {identities}")
    return identities


def _await_identity(fleet: Fleet, index: int) -> str:
    variables = fleet.variables(index)
    command = [*fleet.compose(index, bootstrap=True), "exec", "-T", "worker", "python", "-c", WORKER_CREDENTIAL_PROBE]
    deadline = time.monotonic() + WORKER_REGISTRATION_TIMEOUT_SECONDS
    last_error = "credential probe did not run"
    while time.monotonic() < deadline:
        result = subprocess.run(command, capture_output=True, text=True, check=False, env={**os.environ, **variables})
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[-1]
        last_error = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        time.sleep(WORKER_REGISTRATION_POLL_SECONDS)
    raise TimeoutError(f"release E2E Worker {index} credentials were not durably acknowledged: {last_error}")


async def verify_console_registration(
    client: httpx.AsyncClient,
    token: str,
    baseline: set[str],
    *,
    expected: list[str],
) -> None:
    """控制台新增的 Worker 必须与容器自报身份**完全相等**——多一个都算异常注册。"""
    deadline = time.monotonic() + WORKER_REGISTRATION_TIMEOUT_SECONDS
    wanted = set(expected)
    observed: set[str] = set()
    while time.monotonic() < deadline:
        workers = await get_workers(client, token)
        observed = {str(worker["id"]) for worker in workers} - baseline
        if observed == wanted:
            return
        if not observed <= wanted:
            raise RuntimeError(f"unexpected Workers registered during release E2E: {sorted(observed - wanted)}")
        await asyncio.sleep(WORKER_REGISTRATION_POLL_SECONDS)
    raise TimeoutError(f"release E2E Workers did not all register: want={sorted(wanted)} got={sorted(observed)}")


def promote_to_identity_certificates(fleet: Fleet, identities: list[str]) -> None:
    """撤掉安装 Key，用各自分配到的 worker_id 作 CN 重签客户端证书后重新拉起。"""
    for index, worker_id in zip(fleet.indexes(), identities, strict=True):
        _promote_one(fleet, index, worker_id)


def _promote_one(fleet: Fleet, index: int, worker_id: str) -> None:
    variables = fleet.variables(index)
    bootstrap_compose = fleet.compose(index, bootstrap=True)
    compose_run([*bootstrap_compose, "stop", "worker"], env=variables)
    compose_run([*bootstrap_compose, "rm", "-f", "worker"], env=variables)
    write_worker_identity_certificate(
        fleet.state_dir,
        worker_id,
        directory=Path(variables["ANTCODE_WORKER_TLS_DIR"]).name,
    )
    Path(variables["ANTCODE_WORKER_INSTALL_KEY_FILE"]).unlink()
    compose_run(
        [*fleet.compose(index), "up", "-d", "--wait", "--wait-timeout", str(WORKER_START_TIMEOUT_SECONDS), "worker"],
        env=variables,
    )


def export_identities(fleet: Fleet, identities: list[str]) -> None:
    (fleet.state_dir / "worker-id").write_text(identities[0], encoding="utf-8")
    (fleet.state_dir / "worker-ids.json").write_text(json.dumps(identities), encoding="utf-8")
