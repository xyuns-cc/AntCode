#!/usr/bin/env python3
"""Publish redacted Compose diagnostics as a GitHub error annotation."""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

ANNOTATION_LIMIT = 30_000
LOG_TAIL_LINES = 200
SECRET_NAMES = frozenset(
    {
        "POSTGRES_PASSWORD",
        "REDIS_PASSWORD",
        "JWT_SECRET",
        "ENCRYPTION_KEY",
        "ENCRYPTION_KEY_SALT",
        "DEFAULT_ADMIN_PASSWORD",
        "ANTCODE_WORKER_KEY",
    }
)
STACK_SERVICES = ("postgres", "redis", "web-api", "master", "gateway", "worker", "frontend")


def _load_secrets(path: Path) -> tuple[str, ...]:
    values = [os.environ[name] for name in SECRET_NAMES if os.environ.get(name)]
    if not path.is_file():
        return tuple(values)
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name in SECRET_NAMES and value:
            values.append(value)
    return tuple(dict.fromkeys(values))


def _redact(text: str, secrets: tuple[str, ...]) -> str:
    redacted = text
    for secret in secrets:
        redacted = redacted.replace(secret, "***")
    return redacted


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return f"$ {shlex.join(command)}\n{result.stdout}{result.stderr}".strip()


def _collect(compose_args: list[str]) -> str:
    base = ["docker", "compose", *compose_args]
    status = _run([*base, "ps", "-a"])
    logs = _run([*base, "logs", "--no-color", f"--tail={LOG_TAIL_LINES}", *STACK_SERVICES])
    gateway = _run(["docker", "logs", f"--tail={LOG_TAIL_LINES}", "antcode-worker-gateway-smoke"])
    return "\n\n".join((status, logs, gateway))


def _escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> None:
    compose_args = shlex.split(os.environ["COMPOSE_ARGS"])
    diagnostics = _redact(_collect(compose_args), _load_secrets(Path(".env")))
    escaped = _escape(diagnostics[-ANNOTATION_LIMIT:])
    print(f"::error title=E2E compose diagnostics::{escaped}")


if __name__ == "__main__":
    main()
