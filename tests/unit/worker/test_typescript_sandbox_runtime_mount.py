"""TS 任务的执行计划必须让沙箱挂得到 node 的安装根。

修复前 CodePlugin 把 argv[0] 定成 workspace 里的 ``node_modules/.bin/<runner>``，而
``sandbox_executables`` 是从 payload 可执行文件反推要挂载的安装根的：runner 位于
work_dir 内，会被判成"已经可见"而直接跳过，于是镜像里 node 的 mise 安装根一个都不
进 namespace。runner 的 ``#!/usr/bin/env node`` shebang 随即在沙箱内解析失败。

真机复现（antcode-worker:dev，bwrap + tsx 4.23.12）：
``/usr/bin/env: 'node': No such file or directory``，退出码 127——又一次把交付缺陷
伪装成任务自身的错误，与 79162e0 修掉的裸命令名 fallback 是同一类。

本文件把"计划里的 argv[0] 能反推出 node 安装根"钉成不变量：它同时覆盖插件层选谁当
argv[0] 与沙箱层据此算出哪些挂载根，任何一侧退回去都变红。
"""

from pathlib import Path

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, TaskPayload
from antcode_worker.executor.sandbox_executables import executable_mount_roots
from antcode_worker.plugins.code.plugin import CodePlugin

# 与镜像里 mise 的实际布局一致：<MISE_DATA_DIR>/installs/<language>/<version>/bin
_NODE_VERSION = "22.23.2"
_RUN_CONTEXT = RunContext("run-1", "task-1", "project-1")


@pytest.fixture
def mise_node_install_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """按镜像布局造一个 mise node 安装根，并把 PATH 收窄到只能解析到它。

    不 monkeypatch ``shutil.which``：被替换的正是要验证的那次解析。
    """
    mise_root = (tmp_path / "mise").resolve()
    install_root = mise_root / "installs" / "node" / _NODE_VERSION
    bin_dir = install_root / "bin"
    bin_dir.mkdir(parents=True)
    node = bin_dir / "node"
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("MISE_DATA_DIR", str(mise_root))
    return install_root


def _typescript_workspace(tmp_path: Path) -> Path:
    workspace = (tmp_path / "workspace").resolve()
    runner = workspace / "node_modules" / ".bin" / "tsx"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    runner.chmod(0o755)
    return workspace


@pytest.mark.asyncio
async def test_typescript_plan_lets_the_sandbox_mount_the_node_install_root(
    tmp_path: Path,
    mise_node_install_root: Path,
) -> None:
    workspace = _typescript_workspace(tmp_path)
    data_root = (tmp_path / "data").resolve()
    data_root.mkdir()
    payload = TaskPayload(
        task_type=TaskType.CODE,
        entry_point="main.ts",
        project_cwd=str(workspace),
        workspace_path=str(workspace),
    )

    plan = await CodePlugin().build_plan(_RUN_CONTEXT, payload)
    command = Path(plan.command)
    mount_roots = executable_mount_roots(
        (command, command.resolve()),
        None,
        work_dir=workspace,
        runtime_dir=None,
        data_root=data_root,
    )

    # 修复前 argv[0] 是 work_dir 内的 runner，沙箱一个安装根都算不出来（空元组）。
    assert mise_node_install_root in mount_roots
