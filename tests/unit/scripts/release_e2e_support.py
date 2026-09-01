"""发布 E2E 环境生成的共用夹具：一律走真实的 prepare 入口，不手写 production.env。

判据要验的就是"端口从哪来"，测试自己再手写一份 env 文件，验的就是测试而不是脚本。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import prepare_local_release_e2e
from scripts.release_e2e_endpoints import compose_variables

SERVICES = ("web-api", "master", "gateway", "worker", "frontend")
RUNTIMES = ("postgres", "redis", "reverse-proxy")
REVISION = "a" * 40
IMAGE_TAG = "gateway-e2e"
SOURCE_URL = "https://github.com/example/antcode.git"
DIGEST_LENGTH = 64
ENVIRONMENT_FILE = "production.env"


def digest(character: str) -> str:
    return f"sha256:{character * DIGEST_LENGTH}"


def write_runtime_lock(root: Path) -> Path:
    lock = root / "runtime.json"
    images = {name: f"registry.example/{name}@{digest('d')}" for name in RUNTIMES}
    lock.write_text(json.dumps({"schema_version": 1, "images": images}), encoding="utf-8")
    return lock


def run_prepare(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    workers: int = 1,
    ports: dict[str, int] | None = None,
) -> tuple[Path, Path]:
    """跑一次 `[1/7]`。``ports`` 为空时一个端口参数都不传，走脚本自己的默认值。"""
    state = root / "state"
    runner_env = root / "runner.env"
    overrides = [item for name, value in (ports or {}).items() for item in (f"--{name}", str(value))]
    argv = [
        "prepare_local_release_e2e",
        "--output-dir",
        str(state),
        "--runtime-lock",
        str(write_runtime_lock(root)),
        "--image-tag",
        IMAGE_TAG,
        "--source-url",
        SOURCE_URL,
        "--source-ref",
        REVISION,
        "--runner-env",
        str(runner_env),
        "--workers",
        str(workers),
        *overrides,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    prepare_local_release_e2e.main()
    return state, runner_env


def environment_file(state: Path) -> Path:
    return state / ENVIRONMENT_FILE


def environment_values(state: Path) -> dict[str, str]:
    return compose_variables(environment_file(state))
