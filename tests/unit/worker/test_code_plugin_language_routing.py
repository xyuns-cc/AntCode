"""CodePlugin 的语言路由：声明与后缀的两个信号必须一致，缺信号即拒绝。

修复前的判据是"显式 kwargs.language 优先，取不到就看后缀，无后缀降级 Python，
未知声明静默忽略"。前两条各自制造了一个可复现故障：
- 界面选 Java、入口写 ``app-runner``（无后缀）→ 按 Python 跑，报 python_path 缺失；
- 界面选 TypeScript、入口写 ``app.js`` → 声明悄悄胜出，去找 tsx 跑一个 JS 文件。
本文件锁死新判据，并且不 monkeypatch 被验证的解析函数本身（缺运行时就真的清空 PATH）。
"""

from pathlib import Path

import pytest
from antcode_contracts.execution_language import ExecutionLanguageError
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, RuntimeSpec, TaskPayload
from antcode_worker.plugins.code.plugin import CodePlugin
from antcode_worker.runtime.node_dependency_errors import (
    NODE_DEP_TS_RUNNER_MISSING,
    NodeDependencyInstallError,
)

_PYTHON_CONTEXT = RunContext(
    "run-1",
    "task-1",
    "project-1",
    runtime_spec=RuntimeSpec(python_path="/opt/antcode/runtime/bin/python"),
)


@pytest.fixture
def isolated_bin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _executable(bin_dir: Path, name: str) -> Path:
    path = bin_dir / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _payload(entry_point: str, workspace: Path, language: str | None = None) -> TaskPayload:
    kwargs = {} if language is None else {"language": language}
    return TaskPayload(
        task_type=TaskType.CODE,
        entry_point=entry_point,
        project_cwd=str(workspace),
        workspace_path=str(workspace),
        kwargs=kwargs,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.mark.asyncio
async def test_suffixless_entry_uses_the_dispatched_language(workspace: Path, isolated_bin: Path) -> None:
    """无后缀入口曾恒定当 Python 跑；现在由 Master 下发的 language 定夺。"""
    java = _executable(isolated_bin, "java")

    plan = await CodePlugin().build_plan(_PYTHON_CONTEXT, _payload("app-runner", workspace, "java"))

    assert plan.command == str(java)
    assert plan.args == ["-jar", "app-runner"]


@pytest.mark.asyncio
async def test_suffixless_entry_without_language_is_refused(workspace: Path) -> None:
    with pytest.raises(ExecutionLanguageError, match="无法确定运行时"):
        await CodePlugin().build_plan(_PYTHON_CONTEXT, _payload("app-runner", workspace))


@pytest.mark.asyncio
async def test_declared_language_contradicting_the_entry_suffix_is_refused(workspace: Path) -> None:
    """声明 TypeScript 但入口是 .js：修复前声明胜出，会拿 tsx 去跑 JS。"""
    with pytest.raises(ExecutionLanguageError, match="请把两者改到一致"):
        await CodePlugin().build_plan(_PYTHON_CONTEXT, _payload("app.js", workspace, "typescript"))


@pytest.mark.asyncio
async def test_unknown_declared_language_is_refused_not_silently_ignored(workspace: Path) -> None:
    """未知声明曾被 dict.get 吞掉后落回后缀，于是按 Python 跑得"看起来正常"。"""
    with pytest.raises(ExecutionLanguageError, match="不支持的执行语言"):
        await CodePlugin().build_plan(_PYTHON_CONTEXT, _payload("main.py", workspace, "klingon"))


def test_validate_reports_the_language_conflict_instead_of_raising(workspace: Path) -> None:
    errors = CodePlugin().validate(_payload("main.py", workspace, "go"))

    assert len(errors) == 1
    assert "请把两者改到一致" in errors[0]


@pytest.mark.asyncio
async def test_missing_ts_runner_error_points_at_the_only_workable_path(workspace: Path, isolated_bin: Path) -> None:
    """旧报错只说"需要装 tsx"，把人引向"提交 node_modules"这条必死的路。

    source bundle 不收符号链接，而 ``node_modules/.bin/<runner>`` 永远是符号链接，
    所以报错必须点名真正可行的做法（lockfile + .antcode-deps 离线缓存），
    并且带结构化码而不是让调用方去匹配中文。
    """
    _executable(isolated_bin, "node")

    with pytest.raises(NodeDependencyInstallError) as excinfo:
        await CodePlugin().build_plan(_PYTHON_CONTEXT, _payload("main.ts", workspace, "typescript"))

    assert excinfo.value.error_code == NODE_DEP_TS_RUNNER_MISSING
    assert ".antcode-deps/npm-cache" in excinfo.value.detail
    assert "符号链接" in excinfo.value.detail


@pytest.mark.asyncio
async def test_agreeing_signals_still_route_normally(workspace: Path, isolated_bin: Path) -> None:
    node = _executable(isolated_bin, "node")

    plan = await CodePlugin().build_plan(_PYTHON_CONTEXT, _payload("main.mjs", workspace, "javascript"))

    assert plan.command == str(node)
    assert plan.args == ["main.mjs"]
