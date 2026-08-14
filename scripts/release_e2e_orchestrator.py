"""Start exact production release images and bootstrap an isolated Worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx

from scripts.release_e2e_environment import RUNTIME_SERVICES
from scripts.release_e2e_pki import write_worker_identity_certificate
from tests.e2e.conftest import E2EConfig
from tests.e2e.helpers import extract_data, get_workers, login, request_json

ROOT = Path(__file__).resolve().parents[1]
CONTROL_PROJECT = "antcode-release-control"
WORKER_PROJECT = "antcode-release-worker"
PUBLIC_API_ORIGIN = "https://localhost"
WORKER_REGISTRATION_TIMEOUT_SECONDS = 180
WORKER_REGISTRATION_POLL_SECONDS = 3
WORKER_CREDENTIAL_PROBE = """
import json
import os
from pathlib import Path

directory = Path("/app/data/worker/secrets")
credentials = json.loads((directory / "worker_credentials.json").read_text(encoding="utf-8"))
required = ("worker_id", "api_key", "secret_key", "registration_id")
assert credentials["worker_id"] == os.environ["EXPECTED_WORKER_ID"]
assert all(credentials.get(name) for name in required)
assert not (directory / "worker_registration_intent.json").exists()
"""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    return parser.parse_args()


def _compose(environment: Path, project: str, *files: str) -> list[str]:
    command = ["docker", "compose", "--env-file", str(environment), "-p", project]
    for file_name in files:
        command.extend(("-f", str(ROOT / file_name)))
    return command


def _control(environment: Path, *, admin_bootstrap: bool = False) -> list[str]:
    files = ["infra/docker/docker-compose.prod.yml", "infra/docker/docker-compose.prod.e2e-control.yml"]
    if admin_bootstrap:
        files.append("infra/docker/docker-compose.prod.bootstrap-admin.yml")
    return _compose(environment, CONTROL_PROJECT, *files)


def _worker(environment: Path, *, bootstrap: bool = False) -> list[str]:
    files = ["infra/docker/docker-compose.prod.worker.yml", "infra/docker/docker-compose.prod.e2e-worker.yml"]
    if bootstrap:
        files.append("infra/docker/docker-compose.prod.bootstrap-worker.yml")
    return _compose(environment, WORKER_PROJECT, *files)


def _run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(command, check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def _admin_config(password: str) -> E2EConfig:
    return cast(E2EConfig, SimpleNamespace(admin_user="admin", admin_password=password))


def _validate_exact_images(environment: Path, state_dir: Path) -> None:
    expected = json.loads((state_dir / "release-images.json").read_text(encoding="utf-8"))
    control_config = json.loads(_run([*_control(environment), "config", "--format", "json"], capture=True))
    worker_config = json.loads(_run([*_worker(environment), "config", "--format", "json"], capture=True))
    service_images = {name: service["image"] for name, service in control_config["services"].items()}
    checks = {
        "postgres": expected["postgres"],
        "redis": expected["redis"],
        "web-api": expected["web-api"],
        "master": expected["master"],
        "gateway": expected["gateway"],
        "frontend": expected["frontend"],
        "reverse-proxy": expected["reverse-proxy"],
    }
    for service, image in checks.items():
        if service_images.get(service) != image:
            raise RuntimeError(f"production Compose image mismatch: {service}")
    if worker_config["services"]["worker"]["image"] != expected["worker"]:
        raise RuntimeError("production Worker image mismatch")


def _start_control(environment: Path) -> None:
    control = _control(environment)
    # 只拉按 digest pin 的第三方镜像。不能用 `--ignore-buildable`：它只跳过自身声明了
    # build 段的服务，migration / crawl-redis-upgrade 引用本地构建的 Web API 镜像却没有
    # build 段，会被拿去 registry 解析并失败（真机实测 403）。
    _run([*control, "pull", *RUNTIME_SERVICES])
    _run([*control, "up", "-d", "--wait", "--wait-timeout", "300", "postgres", "redis"])
    _run([*_control(environment, admin_bootstrap=True), "run", "--rm", "--no-deps", "migration"])
    _run([*control, "run", "--rm", "--no-deps", "migration"])
    services = ("web-api", "master", "gateway", "frontend", "reverse-proxy")
    _run([*control, "up", "-d", "--wait", "--wait-timeout", "600", *services])


async def _create_install_key(state_dir: Path) -> tuple[set[str], str]:
    password = (state_dir / "secrets/default_admin_password").read_text(encoding="utf-8")
    config = _admin_config(password)
    verify = str(state_dir / "public-ca.crt")
    async with httpx.AsyncClient(base_url=PUBLIC_API_ORIGIN, verify=verify, timeout=30.0) as client:
        token = await login(client, config)
        baseline = {str(worker["id"]) for worker in await get_workers(client, token)}
        payload = await request_json(
            client,
            "POST",
            "/workers/generate-install-key",
            token=token,
            json={"os_type": "linux"},
        )
    key = str((extract_data(payload) or {})["key"])
    print(f"::add-mask::{key}")
    return baseline, key


async def _wait_for_new_worker(state_dir: Path, baseline: set[str]) -> str:
    password = (state_dir / "secrets/default_admin_password").read_text(encoding="utf-8")
    config = _admin_config(password)
    verify = str(state_dir / "public-ca.crt")
    deadline = time.monotonic() + WORKER_REGISTRATION_TIMEOUT_SECONDS
    async with httpx.AsyncClient(base_url=PUBLIC_API_ORIGIN, verify=verify, timeout=30.0) as client:
        token = await login(client, config)
        while time.monotonic() < deadline:
            workers = await get_workers(client, token)
            new_ids = [str(worker["id"]) for worker in workers if str(worker["id"]) not in baseline]
            if len(new_ids) == 1:
                return new_ids[0]
            if len(new_ids) > 1:
                raise RuntimeError("multiple unexpected Workers registered during release E2E")
            await asyncio.sleep(WORKER_REGISTRATION_POLL_SECONDS)
    raise TimeoutError("release E2E Worker did not register")


def _wait_for_persisted_credentials(environment: Path, worker_id: str) -> None:
    command = [
        *_worker(environment, bootstrap=True),
        "exec",
        "-T",
        "-e",
        f"EXPECTED_WORKER_ID={worker_id}",
        "worker",
        "python",
        "-c",
        WORKER_CREDENTIAL_PROBE,
    ]
    deadline = time.monotonic() + WORKER_REGISTRATION_TIMEOUT_SECONDS
    last_error = "credential probe did not run"
    while time.monotonic() < deadline:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return
        last_error = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        time.sleep(WORKER_REGISTRATION_POLL_SECONDS)
    raise TimeoutError(f"release E2E Worker credentials were not durably acknowledged: {last_error}")


def _bootstrap_worker(environment: Path, state_dir: Path, install_key: str) -> None:
    key_file = state_dir / "secrets/worker_install_key"
    key_file.write_text(install_key, encoding="utf-8")
    key_file.chmod(0o600)
    _run([*_worker(environment, bootstrap=True), "up", "-d", "worker"])


def _restart_without_install_key(environment: Path, state_dir: Path, worker_id: str) -> None:
    bootstrap = _worker(environment, bootstrap=True)
    _run([*bootstrap, "stop", "worker"])
    _run([*bootstrap, "rm", "-f", "worker"])
    write_worker_identity_certificate(state_dir, worker_id)
    (state_dir / "secrets/worker_install_key").unlink()
    _run([*_worker(environment), "up", "-d", "--wait", "--wait-timeout", "300", "worker"])


def _export_worker_id(worker_id: str, state_dir: Path) -> None:
    (state_dir / "worker-id").write_text(worker_id, encoding="utf-8")
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with Path(github_env).open("a", encoding="utf-8") as handle:
            handle.write(f"ANTCODE_E2E_WORKER_ID={worker_id}\n")


async def _main() -> None:
    args = _arguments()
    environment = args.environment.resolve()
    state_dir = args.state_dir.resolve()
    _validate_exact_images(environment, state_dir)
    _start_control(environment)
    baseline, install_key = await _create_install_key(state_dir)
    _bootstrap_worker(environment, state_dir, install_key)
    worker_id = await _wait_for_new_worker(state_dir, baseline)
    _wait_for_persisted_credentials(environment, worker_id)
    _restart_without_install_key(environment, state_dir, worker_id)
    _export_worker_id(worker_id, state_dir)


if __name__ == "__main__":
    asyncio.run(_main())
