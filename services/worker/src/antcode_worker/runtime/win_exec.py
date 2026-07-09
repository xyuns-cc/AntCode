"""Windows-safe subprocess 命令解析。

Windows 上 `asyncio.create_subprocess_exec("npm", ...)` 会直接失败，
因为 npm/pnpm/yarn 实际是 .cmd（batch 文件），不能被 CreateProcess 直接 exec。
必须先用 shutil.which（会遍历 PATHEXT）拿到 `npm.cmd` 的绝对路径。

Linux/macOS 上 shutil.which 一样能拿到绝对路径，逻辑统一。
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Iterable


def resolve_argv(argv: list[str], *, extra_search_paths: Iterable[str] = ()) -> list[str]:
    """把 argv[0] 换成 shutil.which 找到的绝对路径。

    - 已是绝对/相对路径（含 '/' 或 '\\') → 原样返回
    - 找不到 → 原样返回（让 subprocess 抛 FileNotFoundError，方便定位）
    - Windows 上会自动匹配 PATHEXT（.cmd/.exe/.bat）

    ``extra_search_paths`` 会临时前置到 PATH（不影响进程环境）。
    """
    if not argv:
        return argv
    cmd = argv[0]
    if "/" in cmd or "\\" in cmd:
        return argv

    search_path = None
    if extra_search_paths:
        import os
        current = os.environ.get("PATH", "")
        parts = [*extra_search_paths, current] if current else list(extra_search_paths)
        search_path = ";".join(parts) if sys.platform == "win32" else ":".join(parts)

    resolved = shutil.which(cmd, path=search_path)
    if resolved:
        return [resolved, *argv[1:]]
    return argv


def is_windows() -> bool:
    return sys.platform == "win32"
