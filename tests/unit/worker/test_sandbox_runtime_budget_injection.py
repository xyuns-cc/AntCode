"""沙箱内的运行时按宿主尺寸自我配置，而限额按容器预算算——两个数字必须接上。

真机实证（宿主 32GB/8 核，容器 mem_limit=3g/cpus=2，同一份二进制）：bwrap 的
namespace 里没有 ``/sys``，``--proc`` 重挂的 procfs 又是宿主视图，于是

* JVM 初始堆 48MiB → **504MiB**、最大堆 768MiB → **8024MiB**
* Go GOMAXPROCS 2 → **8**
* V8 heap_size_limit 1584MB → **4144MB**

而 ``RLIMIT_DATA`` 是 3GiB×70%÷4 = 537MB。JVM 光初始堆就超了，``java -jar`` 恒挂在
``os::commit_memory ... errno=12``。门槛随宿主内存上涨（32GB 宿主实测 610MB），
所以只能把预算注给运行时，不能调大限额。

本文件全部断言落在 ``CodePlugin.build_plan`` 的产物（argv 与 env）上——那是真正会
被 ``create_subprocess_exec`` 拿去执行的东西。**边界必须说清**：PATH 上的 stub 只能
证明"argv/env 怎么拼"，证明不了运行时真的起得来；后者只有真机能证。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, RuntimeSpec, TaskPayload
from antcode_worker.plugins.code.plugin import CodePlugin
from antcode_worker.runtime import go_execution_policy, runtime_budget
from antcode_worker.runtime.dependency_process import DependencyCommandResult
from antcode_worker.runtime.runtime_budget import RuntimeBudgetUnknownError

# 两台真机 Worker 的生效单任务限额：worker-03 = 3GiB×70%÷4，worker-01 = 4GiB×70%÷2。
_WORKER03_LIMIT_MB = 537
_WORKER01_LIMIT_MB = 1433
_BYTES_PER_MIB = 1024 * 1024
# cgroup v2 的 "<quota> <period>"；两档配额都远小于测试里假造的宿主核数
_QUOTA_TWO_CORES = "200000 100000\n"
_QUOTA_FOUR_CORES = "400000 100000\n"
_QUOTA_FOUR_CORES_VALUE = 4
_FAKE_HOST_CORES = 64


def _context(memory_limit_mb: int) -> RunContext:
    return RunContext(
        "run-1",
        "task-1",
        "project-1",
        memory_limit_mb=memory_limit_mb,
        runtime_spec=RuntimeSpec(python_path="/opt/antcode/runtime/bin/python"),
    )


def _stub_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *names: str) -> Path:
    """把 PATH 收窄到一个只含占位可执行文件的目录。

    换 PATH 而不是 patch ``shutil.which``：后者会把被测的解析逻辑一起短路掉，测试
    也就不再依赖宿主装没装 java/go/node。
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def _workspace(tmp_path: Path, entry: str, *, runner: str | None = None) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / entry).write_text("", encoding="utf-8")
    if runner:
        runner_dir = workspace / "node_modules" / ".bin"
        runner_dir.mkdir(parents=True, exist_ok=True)
        (runner_dir / runner).write_text("", encoding="utf-8")
    return workspace


def _payload(entry: str, workspace: Path, language: str) -> TaskPayload:
    return TaskPayload(
        task_type=TaskType.CODE,
        entry_point=entry,
        workspace_path=str(workspace),
        project_cwd=str(workspace),
        kwargs={"language": language},
    )


def _capture_dependency_env(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """拦下装配期子进程（真沙箱要 bwrap，单测跑不了），只留下它拿到的 env。"""
    seen: list[dict[str, str]] = []

    async def fake_run(command, *, cwd, env, limits):
        del command, cwd, limits
        seen.append(dict(env))
        return DependencyCommandResult(0, "", "")

    monkeypatch.setattr(go_execution_policy, "run_dependency_command", fake_run)
    return seen


# ---------- Java：MaxRAM 的值与位置 ----------


@pytest.mark.asyncio
async def test_java_vm_arg_carries_the_effective_limit_and_precedes_the_jar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-XX:MaxRAM`` 必须存在，且必须排在 ``-jar`` 之前才是 VM 参数。

    位置错了不会报错，只会静默变成被执行程序的 argv——JVM 依旧按宿主 32GB 定尺寸。
    """
    _stub_path(tmp_path, monkeypatch, "java")
    workspace = _workspace(tmp_path, "main.jar")

    plan = await CodePlugin().build_plan(_context(_WORKER03_LIMIT_MB), _payload("main.jar", workspace, "java"))

    expected = f"-XX:MaxRAM={_WORKER03_LIMIT_MB * _BYTES_PER_MIB}"
    assert expected in plan.args
    assert plan.args.index(expected) < plan.args.index("-jar")


@pytest.mark.asyncio
async def test_java_budget_follows_the_worker_limit_not_the_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一份 jar 在两台真机限额下必须拿到两个不同的 MaxRAM。

    这是"值来自哪个来源"的判据：注入的数字若来自宿主 /proc（本机 32GB 恒定），
    两次结果会一模一样。
    """
    _stub_path(tmp_path, monkeypatch, "java")
    workspace = _workspace(tmp_path, "main.jar")
    payload = _payload("main.jar", workspace, "java")

    small = await CodePlugin().build_plan(_context(_WORKER03_LIMIT_MB), payload)
    large = await CodePlugin().build_plan(_context(_WORKER01_LIMIT_MB), payload)

    assert small.args[0] == f"-XX:MaxRAM={_WORKER03_LIMIT_MB * _BYTES_PER_MIB}"
    assert large.args[0] == f"-XX:MaxRAM={_WORKER01_LIMIT_MB * _BYTES_PER_MIB}"


# ---------- Node / TypeScript：V8 老生代上限的值与位置 ----------


@pytest.mark.asyncio
async def test_node_old_space_cap_precedes_the_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_path(tmp_path, monkeypatch, "node")
    workspace = _workspace(tmp_path, "main.js")

    plan = await CodePlugin().build_plan(_context(_WORKER03_LIMIT_MB), _payload("main.js", workspace, "javascript"))

    expected = f"--max-old-space-size={_WORKER03_LIMIT_MB // 2}"
    assert plan.args.index(expected) < plan.args.index("main.js")


@pytest.mark.asyncio
async def test_typescript_old_space_cap_precedes_the_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TS 的 argv 是 ``node <runner> <entry>``；V8 参数落到 runner 之后就成了 runner 的位置参数。"""
    _stub_path(tmp_path, monkeypatch, "node")
    workspace = _workspace(tmp_path, "main.ts", runner="tsx")

    plan = await CodePlugin().build_plan(_context(_WORKER03_LIMIT_MB), _payload("main.ts", workspace, "typescript"))

    runner = str(workspace / "node_modules" / ".bin" / "tsx")
    assert plan.args.index(f"--max-old-space-size={_WORKER03_LIMIT_MB // 2}") < plan.args.index(runner)


# ---------- Go：GOMAXPROCS 的来源 ----------


async def _go_plan_on_a_two_core_quota(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """宿主核数抬高、cgroup 配额钉在 2 核，返回 (编译期 env 列表, 执行期 env)。"""
    import psutil

    quota = tmp_path / "cpu.max"
    quota.write_text(_QUOTA_TWO_CORES, encoding="utf-8")
    monkeypatch.setattr("antcode_worker.resource_budget.CGROUP_V2_CPU_MAX", quota)
    monkeypatch.setattr(psutil, "cpu_count", lambda *a, **k: _FAKE_HOST_CORES)

    _stub_path(tmp_path, monkeypatch, "go")
    build_envs = _capture_dependency_env(monkeypatch)
    workspace = _workspace(tmp_path, "main.go")
    (workspace / "go.mod").write_text("module probe\n\ngo 1.26\n", encoding="utf-8")

    plan = await CodePlugin().build_plan(_context(_WORKER03_LIMIT_MB), _payload("main.go", workspace, "go"))
    return build_envs, plan.env


@pytest.mark.asyncio
async def test_go_compile_runs_at_the_cgroup_cpu_quota_not_the_host_cores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``go build -p`` 与 ``cmd/compile -c`` 都跟着 GOMAXPROCS 走，它是编译期内存的乘数。

    真机实测同一份源码：GOMAXPROCS 未设(=宿主 8 核) 时 ``go build`` 整棵进程树峰值
    RSS 560MB、并发 compile 8 个；钉到容器配额 2 核后降到 314MB、并发 2 个。前者随
    宿主核数上涨，后者不随。
    """
    build_envs, _ = await _go_plan_on_a_two_core_quota(tmp_path, monkeypatch)

    assert [env["GOMAXPROCS"] for env in build_envs] == ["2"]


@pytest.mark.asyncio
async def test_go_binary_executes_at_the_cgroup_cpu_quota_not_the_host_cores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """产物开跑时同样读不到 cgroup：Go 调度器的 P 数也必须由我们补上。"""
    _, exec_env = await _go_plan_on_a_two_core_quota(tmp_path, monkeypatch)

    assert exec_env["GOMAXPROCS"] == "2"


# ---------- 限额不可知：显式失败，不静默按宿主尺寸放行 ----------


@pytest.mark.asyncio
async def test_unknown_effective_limit_refuses_to_plan_instead_of_falling_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**必须失败的控制组**：限额不可知时安静地不注入，就等于放一个按宿主 32GB
    定尺寸的 JVM 进 3GiB 容器，随后被 cgroup 打死——正是要消灭的静默失败。"""
    _stub_path(tmp_path, monkeypatch, "java")
    workspace = _workspace(tmp_path, "main.jar")

    with pytest.raises(RuntimeBudgetUnknownError, match="单任务内存限额不可知"):
        await CodePlugin().build_plan(_context(0), _payload("main.jar", workspace, "java"))


@pytest.mark.asyncio
async def test_python_plan_stays_untouched_under_the_same_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**必须成功的控制组**：同一条 build_plan 在同一份预算下照常出计划。

    Python 走 venv 解释器、不吃 MaxRAM/老生代那一套，argv 必须一个字节都不变；
    它同时证明上面那些红不是"build_plan 整体挂了"造成的。
    """
    _stub_path(tmp_path, monkeypatch, "python3")
    workspace = _workspace(tmp_path, "main.py")

    plan = await CodePlugin().build_plan(_context(_WORKER03_LIMIT_MB), _payload("main.py", workspace, "python"))

    assert plan.command == "/opt/antcode/runtime/bin/python"
    assert plan.args == ["main.py"]
    assert "GOMAXPROCS" not in plan.env


def test_budget_reports_the_container_quota_not_the_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``resolve_runtime_budget`` 的两个字段各自来自哪里，直接钉死。"""
    import psutil

    quota = tmp_path / "cpu.max"
    quota.write_text(_QUOTA_FOUR_CORES, encoding="utf-8")
    monkeypatch.setattr("antcode_worker.resource_budget.CGROUP_V2_CPU_MAX", quota)
    monkeypatch.setattr(psutil, "cpu_count", lambda *a, **k: _FAKE_HOST_CORES)

    budget = runtime_budget.resolve_runtime_budget(_WORKER01_LIMIT_MB)

    assert budget.memory_mb == _WORKER01_LIMIT_MB
    assert budget.memory_bytes == _WORKER01_LIMIT_MB * _BYTES_PER_MIB
    assert budget.cpu_cores == _QUOTA_FOUR_CORES_VALUE
