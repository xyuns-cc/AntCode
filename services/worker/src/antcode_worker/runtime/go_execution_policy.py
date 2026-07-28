"""Go dependency state that is safe to expose inside the task sandbox."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from antcode_worker.runtime.uv_manager import run_command

_CACHE_ROOT = ".antcode-go-cache"
_GO_MOD_DOWNLOAD_TIMEOUT_SECONDS = 600
_GO_INSTALL_ENV_KEYS = ("PATH", "HOME", "GOPROXY", "GONOSUMDB", "GONOSUMCHECK", "GOSUMDB", "GOPRIVATE")


def build_go_execution_env(cwd: str, env: dict[str, str]) -> dict[str, str]:
    """Return a copy whose Go caches stay inside the current run workspace.

    P2 §4.4: 沙箱内 ``go run`` 默认 ``--unshare-net``，模块缓存必须在执行
    前由 ``install_go_dependencies`` 预热；这里强制 ``GOPROXY=off``，缺依赖
    时立即显式失败，而不是在无网沙箱里等网络超时。
    """
    if not (Path(cwd) / "go.mod").is_file():
        return dict(env)
    cache_root = Path(cwd) / _CACHE_ROOT
    return {
        **env,
        "GOCACHE": str(cache_root / "build"),
        "GOMODCACHE": str(cache_root / "modules"),
        "GOENV": "off",
        "GOTOOLCHAIN": "local",
        "GOPROXY": "off",
        "GOFLAGS": "-mod=mod",
    }


async def install_go_dependencies(cwd: str) -> None:
    """执行前（带网络的 prep 阶段）预取 Go 模块到工作区缓存。

    P2 §4.4: 此前 Go 依赖被交给沙箱内的 ``go run`` 解析，而生产沙箱默认
    ``--unshare-net`` —— 未 vendor 的正常 Go 项目稳定失败。与 Node 依赖
    装配同阶段执行 ``go mod download``，失败显式抛错。
    """
    root = Path(cwd)
    if not (root / "go.mod").is_file():
        return
    if (root / "vendor").is_dir():
        # vendor 模式无需模块缓存，go 会自动使用 vendor 目录。
        return
    go_exe = shutil.which("go")
    if go_exe is None:
        raise RuntimeError("Go 项目依赖预取失败: 找不到 go 可执行文件")
    cache_root = root / _CACHE_ROOT
    install_env = {key: os.environ[key] for key in _GO_INSTALL_ENV_KEYS if key in os.environ}
    install_env.update(
        GOCACHE=str(cache_root / "build"),
        GOMODCACHE=str(cache_root / "modules"),
        GOENV="off",
        GOTOOLCHAIN="local",
    )
    result = await run_command(
        [go_exe, "mod", "download"],
        cwd=cwd,
        env=install_env,
        timeout=_GO_MOD_DOWNLOAD_TIMEOUT_SECONDS,
        inherit_env=False,
    )
    if result.exit_code != 0:
        raise RuntimeError(f"Go 依赖预取失败: {result.stderr or result.stdout}")


__all__ = ["build_go_execution_env", "install_go_dependencies"]
