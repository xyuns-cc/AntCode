"""mise 检测。

Worker 在启动阶段调用 `ensure_mise()`：
- 存在 → 记录路径 + 版本，PATH 注入
- 不存在 → 记录警告但不阻断 Python 项目；多语言任务会明确失败

mise 只能由固定版本、校验摘要的镜像或主机安装流程提供。Worker 运行期绝不下载并
执行远程安装脚本。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger


@dataclass(frozen=True)
class MiseStatus:
    available: bool
    path: str = ""
    version: str = ""
    reason: str = ""


def _candidate_paths() -> list[str]:
    """按优先级列出 mise 可能位置。"""
    paths = []
    which = shutil.which("mise")
    if which:
        paths.append(which)
    home = Path.home()
    for rel in (".local/bin/mise", ".mise/bin/mise", "bin/mise"):
        p = home / rel
        if p.exists():
            paths.append(str(p))
    # Windows 场景
    if sys.platform == "win32":
        for rel in (
            "AppData/Local/Programs/mise/mise.exe",
            "scoop/apps/mise/current/mise.exe",
        ):
            p = home / rel
            if p.exists():
                paths.append(str(p))
    # 去重保序
    return list(dict.fromkeys(paths))


async def _get_version(mise_path: str) -> str:
    """`mise --version` 返回值，失败返回空字符串。

    mise_path 已是绝对路径（来自 shutil.which 或已知位置），无需再解析 Windows 后缀。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            mise_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            return (stdout_b or b"").decode(errors="ignore").strip().splitlines()[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"mise --version 失败 ({mise_path}): {exc}")
    return ""


async def detect_mise() -> MiseStatus:
    """探测 mise；找到即返回，未找到返回 available=False。"""
    for p in _candidate_paths():
        version = await _get_version(p)
        if version:
            return MiseStatus(available=True, path=p, version=version)
    return MiseStatus(available=False, reason="未在 PATH 或常见位置找到 mise")


def _prepend_path(mise_path: str) -> None:
    """把 mise 所在目录加到当前进程 PATH 前缀，保证 subprocess 能找到它。"""
    parent = str(Path(mise_path).resolve().parent)
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if parent in parts:
        return
    os.environ["PATH"] = os.pathsep.join([parent, *parts]) if parts else parent
    logger.debug(f"已把 {parent} 加入 PATH")


async def ensure_mise() -> MiseStatus:
    """Worker 启动时调用；返回最终 mise 状态。

    Returns:
        MiseStatus.available=True 表示 mise 可用（无论是本来就装还是刚装完）；
        available=False 表示 mise 缺失但未阻断启动（Python 项目仍能通过系统 python 跑）。
    """
    status = await detect_mise()
    if status.available:
        logger.info(f"检测到 mise: {status.path} ({status.version})")
        _prepend_path(status.path)
        return status

    logger.warning(
        "mise 未安装；运行期自动下载已禁用。请通过固定版本和摘要校验的主机/镜像流程安装。"
        "多语言（Node/Go/Java）任务将失败；仅系统 Python 可用。"
    )
    return status
