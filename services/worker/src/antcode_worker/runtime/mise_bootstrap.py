"""mise 检测 + 可选自动安装。

Worker 在启动阶段调用 `ensure_mise()`：
- 存在 → 记录路径 + 版本，PATH 注入
- 不存在且 `ANTCODE_MISE_AUTO_INSTALL=true`（默认 false）→ Linux/macOS 走
  官方 curl 脚本安装到 ~/.local/bin；Windows 只提示
- 不存在且自动安装关闭 → 记录警告但不阻断（Python 项目走系统 python 仍可用）

自动安装遵循「不静默失败」原则：网络/权限失败会显式 log error + 抛异常，
调用方按需决定是否阻断启动（默认不阻断）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

MISE_INSTALL_URL = "https://mise.run"
MISE_INSTALL_TIMEOUT = 120  # 秒


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
    seen = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


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


def _auto_install_enabled() -> bool:
    # C4/M2: 默认 false — curl|sh MITM 面 & 离线部署失败面比自动装带来的便利
    # 更大。生产走镜像内置（Dockerfile.worker），裸机首次可临时置 true。
    val = os.environ.get("ANTCODE_MISE_AUTO_INSTALL", "false").strip().lower()
    return val in ("1", "true", "yes", "on")


async def _install_mise_unix() -> MiseStatus:
    """在 Linux/macOS 上运行官方 install script。"""
    logger.warning("mise 未安装，尝试自动安装（curl {}）...", MISE_INSTALL_URL)
    # 使用 sh -c 组合 curl + sh，避免 pipe 语法在 subprocess 中的复杂性
    cmd = ["sh", "-c", f"curl -fsSL {MISE_INSTALL_URL} | sh"]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=MISE_INSTALL_TIMEOUT
        )
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        # kill 后必须等待，否则会留下 zombie 进程占 fd/内存
        with contextlib.suppress(Exception):
            await proc.wait()
        raise RuntimeError(f"mise 自动安装超过 {MISE_INSTALL_TIMEOUT}s 未完成") from None

    if proc.returncode != 0:
        raise RuntimeError(
            f"mise 自动安装失败 (exit={proc.returncode}): "
            f"{(stderr_b or b'').decode(errors='ignore').strip()[:500]}"
        )
    stdout = (stdout_b or b"").decode(errors="ignore")
    logger.info(f"mise 安装脚本输出:\n{stdout[:500]}")

    # 复检
    status = await detect_mise()
    if not status.available:
        raise RuntimeError("mise 安装脚本执行完成但仍无法检测到，可能需要重新登录 shell")
    return status


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

    if not _auto_install_enabled():
        logger.warning(
            "mise 未安装，且 ANTCODE_MISE_AUTO_INSTALL=false 已禁用自动安装。"
            "多语言（Node/Go/Java）任务将失败；仅系统 python 可用。"
        )
        return status

    if sys.platform == "win32":
        logger.warning(
            "mise 未安装。Windows 请手动安装：`winget install jdx.mise` 或 "
            "从 https://mise.jdx.dev/ 下载 binary 到 PATH。当前进程跳过自动安装。"
        )
        return status

    try:
        installed = await _install_mise_unix()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"mise 自动安装失败，多语言任务将不可用: {exc}")
        return MiseStatus(available=False, reason=str(exc))

    logger.info(f"mise 自动安装成功: {installed.path} ({installed.version})")
    _prepend_path(installed.path)
    return installed
