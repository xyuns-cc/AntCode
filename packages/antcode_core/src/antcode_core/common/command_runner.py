"""命令执行工具"""
from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass

from loguru import logger

from antcode_core.common.config import settings


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


def _build_env(env_overrides: dict | None = None) -> dict:
    """构建命令执行环境变量。"""
    base = os.environ.copy()

    mise_data = getattr(settings, "MISE_DATA_ROOT", "")
    if mise_data:
        mise_cache = os.path.join(mise_data, "cache")
        os.makedirs(mise_data, exist_ok=True)
        os.makedirs(mise_cache, exist_ok=True)
        base.setdefault("MISE_DATA_DIR", mise_data)
        base.setdefault("MISE_CACHE_DIR", mise_cache)
        base.setdefault("MISE_TRUSTED_CONFIG_PATHS", "")

    if env_overrides:
        base.update(env_overrides)
    return base


async def run_command(
    args: list[str],
    cwd: str | None = None,
    env_overrides: dict | None = None,
    timeout: int = 900,
) -> CommandResult:
    """异步执行命令并返回结果。

    Args:
        args: 命令参数列表
        cwd: 工作目录
        env_overrides: 环境变量覆盖
        timeout: 超时时间（秒）

    Returns:
        CommandResult: 包含 exit_code, stdout, stderr
    """
    env = _build_env(env_overrides)
    cmd_str = " ".join(args)
    logger.debug(f"执行命令: {cmd_str} 目录={cwd or os.getcwd()}")

    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        return CommandResult(exit_code=124, stdout="", stderr=f"命令超时: {cmd_str}")

    stdout = stdout_b.decode(errors="ignore") if stdout_b else ""
    stderr = stderr_b.decode(errors="ignore") if stderr_b else ""
    return CommandResult(exit_code=process.returncode or 0, stdout=stdout, stderr=stderr)


__all__ = ["CommandResult", "run_command"]
