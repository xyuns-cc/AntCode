"""Go dependency state that is safe to expose inside the task sandbox."""

from __future__ import annotations

import re
from pathlib import Path

from antcode_worker.runtime.dependency_process import DependencyLimits

_CACHE_ROOT = ".antcode-go-cache"
_EXTERNAL_DIRECTIVE_PATTERN = re.compile(r"^(?:require|replace)\b")


def build_go_execution_env(cwd: str, env: dict[str, str]) -> dict[str, str]:
    """Return a copy whose Go caches stay inside the current run workspace.

    沙箱内 ``go run`` 默认 ``--unshare-net``。外部模块必须随源码提交
    ``vendor``；这里强制 ``GOPROXY=off``，缺依赖时立即显式失败。
    """
    if not (Path(cwd) / "go.mod").is_file():
        return dict(env)
    cache_root = Path(cwd) / _CACHE_ROOT
    vendor_mode = (Path(cwd) / "vendor").is_dir()
    return {
        **env,
        "GOCACHE": str(cache_root / "build"),
        "GOMODCACHE": str(cache_root / "modules"),
        "GOENV": "off",
        "GOWORK": "off",
        "GOTOOLCHAIN": "local",
        "GOPROXY": "off",
        "GOFLAGS": "-mod=vendor" if vendor_mode else "-mod=mod",
    }


async def install_go_dependencies(cwd: str, *, limits: DependencyLimits) -> None:
    """Require external modules to be vendored before sandbox execution."""
    del limits
    root = Path(cwd)
    if not (root / "go.mod").is_file():
        return
    if (root / "go.work").exists():
        raise RuntimeError("Go 安全执行不支持 go.work；请提交单模块 source bundle")
    if (root / "vendor").is_dir():
        return
    content = (root / "go.mod").read_text(encoding="utf-8")
    if _has_external_directive(content):
        raise RuntimeError("Go 外部依赖必须提交 vendor 目录；Worker 禁止沙箱外下载模块")


def _has_external_directive(content: str) -> bool:
    for raw_line in content.splitlines():
        line = raw_line.split("//", maxsplit=1)[0].strip()
        if _EXTERNAL_DIRECTIVE_PATTERN.match(line):
            return True
    return False


__all__ = ["build_go_execution_env", "install_go_dependencies"]
