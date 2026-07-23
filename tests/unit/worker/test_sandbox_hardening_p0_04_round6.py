"""P0-04 (round6) 回归:沙箱身份隔离加固。

审查文档 code-review-2026-07-23-round6-review.md P0-04:
1. sandbox.py:253-254 非 bwrap 绝对命令前缀执行,`/usr/bin/env` 可完全绕过隔离
2. sandbox.py:266-268 --ro-bind / / 遮蔽只覆盖已知目录,未覆盖 /etc/antcode/tls
   等 Worker 身份/mTLS 私钥路径

本测试锁死:
1. 任何非 bwrap basename 的绝对沙箱命令一律 raise
2. credential mask 覆盖 Worker mTLS + 身份目录(即使当前路径不存在,check
   源码字符串确保 candidate 清单里有)
"""

from __future__ import annotations

import inspect

import pytest
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig


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


def test_credential_mask_covers_worker_identity_and_mtls():
    """P0-04:credential mask 覆盖 Worker mTLS 私钥挂载点与身份目录。

    这些路径可能在 CI 上不存在(is_dir 过滤会跳过),因此检查源码里 candidate
    清单包含这些路径 —— 只要清单里有,一旦运行时目录存在就会被 tmpfs 掩掉。
    """
    source = inspect.getsource(BasicSandbox._credential_mask_dirs)
    for expected in (
        "/etc/antcode/tls",  # Gateway/Web API mTLS 私钥
        "/etc/antcode",  # Worker YAML 配置 + Direct Redis URL
        "/app/data/worker",  # Worker identity 存储
        "/var/lib/antcode",  # 备用挂载点
    ):
        assert expected in source, f"credential mask 应覆盖 {expected} 但未在 candidates 里"
