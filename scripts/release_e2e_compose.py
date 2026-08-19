"""Compose 命令构造与执行——发布 E2E 的控制面与 Worker 栈共用。

Worker 是"每主机一个进程"的部署单位：``docker-compose.prod.worker.yml`` 里
container_name / data volume / mTLS 目录全部由 env 参数化，所以多 Worker 不是
"一个 compose 文件里加副本",而是**同一份 Worker Compose 起多个 project**，
每个 project 用一组独立的 ``ANTCODE_WORKER_*`` 变量。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROL_PROJECT = "antcode-release-control"
WORKER_PROJECT = "antcode-release-worker"

CONTROL_FILES = ("infra/docker/docker-compose.prod.yml", "infra/docker/docker-compose.prod.e2e-control.yml")
ADMIN_BOOTSTRAP_FILE = "infra/docker/docker-compose.prod.bootstrap-admin.yml"
WORKER_FILES = ("infra/docker/docker-compose.prod.worker.yml", "infra/docker/docker-compose.prod.e2e-worker.yml")
WORKER_BOOTSTRAP_FILE = "infra/docker/docker-compose.prod.bootstrap-worker.yml"


def compose(environment: Path, project: str, *files: str) -> list[str]:
    command = ["docker", "compose", "--env-file", str(environment), "-p", project]
    for file_name in files:
        command.extend(("-f", str(ROOT / file_name)))
    return command


def control(environment: Path, *, admin_bootstrap: bool = False) -> list[str]:
    files = [*CONTROL_FILES]
    if admin_bootstrap:
        files.append(ADMIN_BOOTSTRAP_FILE)
    return compose(environment, CONTROL_PROJECT, *files)


def worker_project(index: int) -> str:
    """index 0 沿用原 project 名，保持单 Worker 拓扑与既有清理脚本完全一致。"""
    return WORKER_PROJECT if index == 0 else f"{WORKER_PROJECT}-{index}"


def worker(environment: Path, index: int = 0, *, bootstrap: bool = False) -> list[str]:
    files = [*WORKER_FILES]
    if bootstrap:
        files.append(WORKER_BOOTSTRAP_FILE)
    return compose(environment, worker_project(index), *files)


def run(command: list[str], *, capture: bool = False, env: dict[str, str] | None = None) -> str:
    """执行 Compose 命令；``env`` 里的变量覆盖 ``--env-file``（shell 环境优先级更高）。"""
    merged = {**os.environ, **env} if env else None
    result = subprocess.run(command, check=True, text=True, capture_output=capture, env=merged)
    return result.stdout if capture else ""
