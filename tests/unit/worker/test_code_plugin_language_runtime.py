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
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, RuntimeSpec, TaskPayload
from antcode_worker.plugins.code.plugin import CodePlugin
from antcode_worker.runtime.language_runtime import LanguageRuntimeMissingError

_RUN_CONTEXT = RunContext("run-1", "task-1", "project-1")


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

    with pytest.raises(RuntimeError, match="无法识别的入口文件类型"):
        await CodePlugin().build_plan(context, _payload("main.rb", workspace))
