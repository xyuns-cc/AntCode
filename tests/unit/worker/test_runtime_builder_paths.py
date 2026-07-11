from pathlib import Path

import pytest
from antcode_worker.runtime import builder as module
from antcode_worker.runtime.builder import RuntimeBuilder
from antcode_worker.runtime.spec import LockSource, RuntimeSpec


@pytest.mark.asyncio
async def test_runtime_builder_writes_dependency_temp_files_under_root_data(monkeypatch):
    captured_args: list[str] = []

    async def fake_run_command(args, env=None, timeout=0):
        del env, timeout
        captured_args.extend(args)
        return module.CommandResult(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(module, "run_command", fake_run_command)

    builder = RuntimeBuilder(str(Path.cwd() / "data" / "worker" / "runtimes"))
    spec = RuntimeSpec(
        lock_source=LockSource(["requests==2.32.0"]),
        constraints=["requests<3"],
    )

    await builder._install_requirements(str(Path.cwd() / "data" / "worker" / "venv"), spec)

    temp_files = [Path(item) for item in captured_args if item.endswith(".txt")]
    assert temp_files
    assert all(Path.cwd() / "data" in item.parents for item in temp_files)
