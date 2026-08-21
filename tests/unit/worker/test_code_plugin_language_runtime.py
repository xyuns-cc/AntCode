"""CodePlugin 对语言运行时缺失必须 fail-closed。

历史实现是 ``shutil.which("node") or "node"``：镜像没装 Node/Go/Java 时照样产出一份
"看起来合法"的执行计划，裸命令名一路带到沙箱层，最终以 ``任务可执行文件不存在: node``
或 exit 127 出现在用户任务日志里。那是交付事故（镜像少了一层运行时），却被伪装成任务
自身的错误。本文件锁死：缺运行时就在计划构建期显式失败，且失败信息指向镜像。

用清空 PATH 的方式制造"缺失"，不 monkeypatch ``shutil.which``——被替换的正是要验证的
那次查找，替换掉它等于把判据换成测试自己的桩。
"""

import os
from pathlib import Path

import pytest
from antcode_contracts.execution_language import ExecutionLanguageError
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, RuntimeSpec, TaskPayload
from antcode_worker.plugins.code.plugin import CodePlugin
from antcode_worker.runtime.language_runtime import LanguageRuntimeMissingError

# memory_limit_mb 用真机 worker-03 的生效限额：CodePlugin 要靠它把预算注进运行时
_RUN_CONTEXT = RunContext("run-1", "task-1", "project-1", memory_limit_mb=537)


@pytest.fixture
def runtime_free_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把 PATH 指向一个空目录，让 shutil.which 真的解析不到任何语言二进制。"""
    bin_dir = tmp_path / "empty-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _payload(entry_point: str, workspace: Path) -> TaskPayload:
    return TaskPayload(
        task_type=TaskType.CODE,
        entry_point=entry_point,
        project_cwd=str(workspace),
        workspace_path=str(workspace),
    )


def _fake_executable(bin_dir: Path, name: str) -> Path:
    path = bin_dir / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _typescript_workspace(tmp_path: Path) -> Path:
    """带 tsx runner 的 workspace（runner 就是一个 shebang 指向 node 的脚本）。"""
    workspace = tmp_path / "workspace"
    runner = workspace / "node_modules" / ".bin" / "tsx"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    runner.chmod(0o755)
    return workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [("main.js", "node"), ("app.jar", "java"), ("main.go", "go")])
async def test_missing_language_runtime_fails_the_plan(
    tmp_path: Path,
    runtime_free_path: Path,
    case: tuple[str, str],
) -> None:
    del runtime_free_path
    entry_point, missing_binary = case
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(LanguageRuntimeMissingError, match=missing_binary):
        await CodePlugin().build_plan(_RUN_CONTEXT, _payload(entry_point, workspace))


@pytest.mark.asyncio
async def test_node_command_is_the_resolved_absolute_path(tmp_path: Path, runtime_free_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    node = _fake_executable(runtime_free_path, "node")

    plan = await CodePlugin().build_plan(_RUN_CONTEXT, _payload("main.js", workspace))

    assert plan.command == str(node)
    assert os.path.isabs(plan.command)


@pytest.mark.asyncio
async def test_typescript_without_node_fails_closed_like_the_other_languages(
    tmp_path: Path,
    runtime_free_path: Path,
) -> None:
    """TS 曾是唯一不解析 node 的分支：runner 在就直接交出去，镜像缺 node 也照样出计划。"""
    del runtime_free_path
    workspace = _typescript_workspace(tmp_path)

    with pytest.raises(LanguageRuntimeMissingError, match="node"):
        await CodePlugin().build_plan(_RUN_CONTEXT, _payload("main.ts", workspace))


@pytest.mark.asyncio
async def test_typescript_runs_the_workspace_runner_through_node(
    tmp_path: Path,
    runtime_free_path: Path,
) -> None:
    """argv[0] 必须是 node 本身；runner 降级成第一个参数（沙箱据 argv[0] 决定挂哪个安装根）。"""
    workspace = _typescript_workspace(tmp_path)
    node = _fake_executable(runtime_free_path, "node")

    plan = await CodePlugin().build_plan(_RUN_CONTEXT, _payload("main.ts", workspace))

    assert plan.command == str(node)
    runner = str(workspace / "node_modules" / ".bin" / "tsx")
    assert plan.args == ["--max-old-space-size=268", runner, "main.ts"]


@pytest.mark.asyncio
async def test_unrecognized_entry_extension_is_rejected_not_run_as_python(tmp_path: Path) -> None:
    """未识别后缀曾静默降级成 Python：用 Python 解释器去跑 .rb 只会得到语法错误。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = RunContext(
        "run-1",
        "task-1",
        "project-1",
        runtime_spec=RuntimeSpec(python_path="/opt/antcode/runtime/bin/python"),
    )

    with pytest.raises(ExecutionLanguageError, match="不属于任何受支持的执行语言"):
        await CodePlugin().build_plan(context, _payload("main.rb", workspace))
