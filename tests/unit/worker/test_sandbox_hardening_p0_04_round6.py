"""P0-04 (round6) 回归:沙箱身份隔离加固。

审查文档 code-review-2026-07-23-round6-review.md P0-04:
1. sandbox.py:253-254 非 bwrap 绝对命令前缀执行,`/usr/bin/env` 可完全绕过隔离
2. sandbox.py:266-268 --ro-bind / / 遮蔽只覆盖已知目录,未覆盖 /etc/antcode/tls
   等 Worker 身份/mTLS 私钥路径

本测试锁死:
1. 任何非 bwrap basename 的绝对沙箱命令一律 raise
2. bwrap 从空根视图按白名单挂载，不再暴露 Worker 数据根
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig


def _host_mounts(args: list[str]) -> set[str]:
    return {args[index + 1] for index, value in enumerate(args[:-2]) if value in {"--ro-bind", "--bind"}}


@pytest.mark.parametrize(
    "sandbox_bin",
    [
        "/usr/bin/env",  # 审查明确点名的攻击向量
        "/usr/bin/firejail",
        "/usr/local/bin/podman",
        "/bin/sh",
        "/usr/bin/python3",  # payload runtime 本身不能作为沙箱
    ],
)
def test_non_bwrap_sandbox_command_rejected(sandbox_bin):
    """P0-04:任何非 bwrap basename 的绝对命令一律 raise,不能前缀绕过隔离。"""
    sandbox = BasicSandbox(SandboxConfig(sandbox_command=[sandbox_bin]))
    with pytest.raises(RuntimeError, match="仅支持 bwrap 沙箱"):
        sandbox.wrap_command(["python", "test.py"], {})


def test_worker_root_and_sibling_runtime_are_absent_from_mounts(tmp_path: Path) -> None:
    data_root = tmp_path / "worker"
    work_dir = data_root / "runs" / "sources" / "run-current" / "project"
    current_runtime = data_root / "runtimes" / "current"
    sibling_runtime = data_root / "runtimes" / "other-tenant"
    sensitive_dirs = tuple(data_root / name for name in ("secrets", "identity", "runs", "temp"))
    for directory in (work_dir, current_runtime, sibling_runtime, *sensitive_dirs):
        directory.mkdir(parents=True, exist_ok=True)
    config_file = data_root / "worker_config.yaml"
    config_file.write_text("transport_mode: gateway\n", encoding="utf-8")

    sandbox = BasicSandbox(SandboxConfig(sandbox_command=["/usr/bin/bwrap"], data_dir=str(data_root)))
    args = sandbox.wrap_command(
        [sys.executable],
        {
            "work_dir": str(work_dir),
            "run_id": "run-current",
            "runtime_path": str(current_runtime),
            "tmpfs_size_mb": 512,
        },
    )

    assert not any(args[index : index + 3] == ["--ro-bind", "/", "/"] for index in range(len(args) - 2))
    mounted_sources = _host_mounts(args)
    assert str(current_runtime.resolve()) in mounted_sources
    assert str(data_root.resolve()) not in mounted_sources
    assert str(sibling_runtime.resolve()) not in mounted_sources
    assert str(config_file.resolve()) not in mounted_sources
    assert all(str(path.resolve()) not in mounted_sources for path in sensitive_dirs)
