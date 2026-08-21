"""Go dependency state that is safe to expose inside the task sandbox."""

from __future__ import annotations

import os
import re
from pathlib import Path

from antcode_worker.runtime.dependency_process import DependencyLimits, run_dependency_command
from antcode_worker.runtime.language_runtime import GO_RUNTIME, require_language_executable
from antcode_worker.runtime.runtime_budget import RuntimeBudget, go_budget_env

_CACHE_ROOT = ".antcode-go-cache"
# 产物落在缓存根下：这里已经是 go 自己往里写 build/modules 的目录，
# 不会和用户源码抢名字，也跟着 run 工作区一起被回收。
_BINARY_PARTS = (_CACHE_ROOT, "bin", "task-binary")
_EXTERNAL_DIRECTIVE_PATTERN = re.compile(r"^(?:require|replace)\b")
# 编译子进程只继承显式列出的宿主变量。run_dependency_command 不做 env 过滤，
# 而任务自带的 env_vars 里可能有凭据，没有任何理由进编译器。
_BUILD_ENV_KEYS = ("PATH",)


def go_binary_path(cwd: str) -> Path:
    """Return the executable that :func:`build_go_binary` produces for ``cwd``."""
    return Path(cwd).joinpath(*_BINARY_PARTS)


def _go_state_env(cwd: str) -> dict[str, str]:
    """Go 缓存与模块解析状态，全部指向当前 run 工作区内。"""
    cache_root = Path(cwd) / _CACHE_ROOT
    vendor_mode = (Path(cwd) / "vendor").is_dir()
    return {
        "GOCACHE": str(cache_root / "build"),
        "GOMODCACHE": str(cache_root / "modules"),
        "GOENV": "off",
        "GOWORK": "off",
        "GOTOOLCHAIN": "local",
        "GOPROXY": "off",
        "GOFLAGS": "-mod=vendor" if vendor_mode else "-mod=mod",
    }


def build_go_execution_env(cwd: str, env: dict[str, str], budget: RuntimeBudget) -> dict[str, str]:
    """Return a copy whose Go caches stay inside the current run workspace.

    沙箱内编译默认 ``--unshare-net``。外部模块必须随源码提交 ``vendor``；
    这里强制 ``GOPROXY=off``，缺依赖时立即显式失败。

    GOMAXPROCS 在 go.mod 之外注入：沙箱里读不到 cgroup 的不只是模块化项目，产物一旦
    开跑，调度器的 P 数同样要按容器配额而不是宿主核数来（见 ``runtime_budget``）。
    """
    runtime_env = {**env, **go_budget_env(budget)}
    if not (Path(cwd) / "go.mod").is_file():
        return runtime_env
    return {**runtime_env, **_go_state_env(cwd)}


async def build_go_binary(
    cwd: str,
    *,
    entry_point: str,
    limits: DependencyLimits,
    budget: RuntimeBudget,
) -> None:
    """Compile ``entry_point`` ahead of execution so the run reports its own exit code.

    ``go run`` 不透传被编译程序的退出码：程序 ``os.Exit(9)`` 时 ``go run`` 自己以 1
    退出，真值只留在 stderr 的 ``exit status 9``。退出码是产品对外的结构化信号
    （重试、告警、编排都按它分支），在这个交接点被折叠等于把信息丢掉。先编译、
    再由 Worker 直接 exec 产物，退出码就是内核报回来的那一个。

    编译与 ``npm ci`` 同走 ``run_dependency_command``：源码不可信（``#cgo`` 指令能把
    参数带进 C 编译器），必须在无网络 bwrap 与同一套资源上限内跑，不能退回宿主直跑。

    ``budget`` 决定 GOMAXPROCS，也就是 ``go build -p`` 的并行编译动作数与
    ``cmd/compile`` 的后端并发：沙箱里 Go 读不到 cgroup，不给就按宿主核数扇出，
    编译期峰值内存随宿主核数上涨而与本任务的内存限额无关。
    """
    go_exe = require_language_executable(GO_RUNTIME)
    binary = go_binary_path(cwd)
    binary.parent.mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in _BUILD_ENV_KEYS if key in os.environ}
    env["HOME"] = cwd
    env.update(_go_state_env(cwd))
    env.update(go_budget_env(budget))
    result = await run_dependency_command(
        [go_exe, "build", "-o", str(binary), entry_point],
        cwd=cwd,
        env=env,
        limits=limits,
    )
    if result.exit_code != 0:
        raise RuntimeError(f"Go 编译失败（go build 退出码 {result.exit_code}）: {result.stderr or result.stdout}")


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


__all__ = [
    "build_go_binary",
    "build_go_execution_env",
    "go_binary_path",
    "install_go_dependencies",
]
