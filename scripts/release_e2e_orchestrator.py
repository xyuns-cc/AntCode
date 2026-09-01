"""Start exact production release images and bootstrap an isolated Worker fleet."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx

from scripts import release_e2e_provenance
from scripts.release_e2e_compose import control as control_compose
from scripts.release_e2e_compose import run as _run
from scripts.release_e2e_compose import worker as worker_compose
from scripts.release_e2e_endpoints import release_endpoints
from scripts.release_e2e_environment import FLEET_FILE, RUNTIME_SERVICES
from scripts.release_e2e_workers import (
    Fleet,
    bootstrap,
    collect_identities,
    create_install_keys,
    export_identities,
    promote_to_identity_certificates,
    verify_console_registration,
)
from tests.e2e.conftest import E2EConfig
from tests.e2e.helpers import get_workers, login

CONTROL_START_TIMEOUT_SECONDS = 600
MIDDLEWARE_START_TIMEOUT_SECONDS = 300


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    return parser.parse_args()


def _fleet(environment: Path, state_dir: Path) -> Fleet:
    description = json.loads((state_dir / FLEET_FILE).read_text(encoding="utf-8"))
    count = int(description["count"])
    if count < 1:
        raise ValueError("release E2E fleet must declare at least one Worker")
    return Fleet(environment=environment, state_dir=state_dir, slug=str(description["slug"]), count=count)


def _admin_config(password: str) -> E2EConfig:
    return cast(E2EConfig, SimpleNamespace(admin_user="admin", admin_password=password))


def _validate_exact_images(environment: Path, state_dir: Path) -> None:
    """第三方运行时镜像验 digest pin；五个应用镜像验它们仍是 `[2/7]` 记下的那个 image ID。

    应用镜像原先比的是**名字**，而期望名与 Compose 里的 `${ANTCODE_IMAGE_TAG}` 同源，
    那条断言恒真、只能验出 Compose 写错服务名——见 release_e2e_provenance 的模块注释。
    """
    expected = json.loads((state_dir / "release-images.json").read_text(encoding="utf-8"))
    control_config = json.loads(_run([*control_compose(environment), "config", "--format", "json"], capture=True))
    worker_config = json.loads(_run([*worker_compose(environment), "config", "--format", "json"], capture=True))
    service_images = {name: service["image"] for name, service in control_config["services"].items()}
    service_images["worker"] = worker_config["services"]["worker"]["image"]
    for service in RUNTIME_SERVICES:
        if service_images.get(service) != expected[service]:
            raise RuntimeError(f"production Compose image mismatch: {service}")
    release_e2e_provenance.validate(state_dir, service_images)


def _start_control(environment: Path) -> None:
    control = control_compose(environment)
    # 只拉按 digest pin 的第三方镜像。不能用 `--ignore-buildable`：它只跳过自身声明了
    # build 段的服务，migration / crawl-redis-upgrade 引用本地构建的 Web API 镜像却没有
    # build 段，会被拿去 registry 解析并失败（真机实测 403）。
    _run([*control, "pull", *RUNTIME_SERVICES])
    middleware_timeout = str(MIDDLEWARE_START_TIMEOUT_SECONDS)
    _run([*control, "up", "-d", "--wait", "--wait-timeout", middleware_timeout, "postgres", "redis"])
    _run([*control_compose(environment, admin_bootstrap=True), "run", "--rm", "--no-deps", "migration"])
    _run([*control, "run", "--rm", "--no-deps", "migration"])
    services = ("web-api", "master", "gateway", "frontend", "reverse-proxy")
    _run([*control, "up", "-d", "--wait", "--wait-timeout", str(CONTROL_START_TIMEOUT_SECONDS), *services])


def _admin_client(fleet: Fleet) -> httpx.AsyncClient:
    """公网入口取本轮真正发布出去的 origin，而不是写死的 `https://localhost`。

    端口一被重映射，写死值就会去登录别人的 :443——共享测试机上那会变成"用别人的栈
    验本轮的发布"，报错还难以解释。
    """
    verify = str(fleet.state_dir / "public-ca.crt")
    origin = release_endpoints(fleet.environment).https_origin
    return httpx.AsyncClient(base_url=origin, verify=verify, timeout=30.0)


async def _bootstrap_fleet(fleet: Fleet) -> list[str]:
    """注册 -> 各自持久化身份 -> 控制台核对 -> 按 worker_id 重签证书走 mTLS。"""
    password = (fleet.state_dir / "secrets/default_admin_password").read_text(encoding="utf-8")
    config = _admin_config(password)
    async with _admin_client(fleet) as client:
        token = await login(client, config)
        baseline = {str(worker["id"]) for worker in await get_workers(client, token)}
        install_keys = await create_install_keys(client, token, fleet.count)
        bootstrap(fleet, install_keys)
        identities = collect_identities(fleet)
        await verify_console_registration(client, token, baseline, expected=identities)
    promote_to_identity_certificates(fleet, identities)
    return identities


def _export_github_env(identities: list[str]) -> None:
    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        return
    with Path(github_env).open("a", encoding="utf-8") as handle:
        handle.write(f"ANTCODE_E2E_WORKER_ID={identities[0]}\n")
        handle.write(f"ANTCODE_E2E_WORKER_IDS={','.join(identities)}\n")


async def _main() -> None:
    args = _arguments()
    fleet = _fleet(args.environment.resolve(), args.state_dir.resolve())
    _validate_exact_images(fleet.environment, fleet.state_dir)
    _start_control(fleet.environment)
    identities = await _bootstrap_fleet(fleet)
    export_identities(fleet, identities)
    _export_github_env(identities)


if __name__ == "__main__":
    asyncio.run(_main())
