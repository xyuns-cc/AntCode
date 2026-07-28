"""Strict environment contract for database initialization."""

import os
import sys
from pathlib import Path

from loguru import logger

MIN_JWT_SECRET_BYTES = 32


async def check_environment() -> None:
    required = ["DATABASE_URL", "ENCRYPTION_KEY"]
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    jwt_error = _jwt_secret_source_error()
    if jwt_error:
        missing.append(jwt_error)
    if not missing:
        return
    logger.error(
        "环境变量缺失: {}。请检查 .env 或环境是否加载。",
        ", ".join(missing),
    )
    sys.exit(1)


def _jwt_secret_source_error() -> str | None:
    inline_secret = os.environ.get("JWT_SECRET", "").strip()
    if inline_secret:
        return _secret_length_error("JWT_SECRET", inline_secret)
    secret_file = os.environ.get("JWT_SECRET_FILE", "").strip()
    if not secret_file:
        return "JWT_SECRET 或 JWT_SECRET_FILE"
    path = Path(secret_file)
    if not path.is_file():
        return f"JWT_SECRET_FILE（文件不存在或不是普通文件: {path}）"
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return f"JWT_SECRET_FILE（文件不可读: {path}）"
    if not secret:
        return f"JWT_SECRET_FILE（文件为空: {path}）"
    return _secret_length_error("JWT_SECRET_FILE", secret)


def _secret_length_error(source: str, secret: str) -> str | None:
    if len(secret.encode("utf-8")) >= MIN_JWT_SECRET_BYTES:
        return None
    return f"{source}（必须至少包含 {MIN_JWT_SECRET_BYTES} 字节）"
