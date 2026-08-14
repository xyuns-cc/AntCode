import inspect

import pytest
from antcode_worker.runtime.builder import RuntimeBuilder
from antcode_worker.runtime.spec import PythonSpec, RuntimeSpec
from antcode_worker.runtime.uv_manager import CommandResult, UVManager


def test_runtime_builder_does_not_fallback_to_standard_venv():
    source = inspect.getsource(RuntimeBuilder._create_venv)

    assert "python_exe" not in source
    assert "sys.executable" not in source
    assert "-m" not in source
    assert "venv" in source


def test_runtime_builder_does_not_fallback_to_pip_install():
    source = inspect.getsource(RuntimeBuilder._install_requirements)

    assert 'python_exe, "-m", "pip"' not in source
    assert "尝试使用 pip" not in source
    assert "uv pip install" in source


def test_uv_manager_uses_uv_pip_without_python_module_fallbacks():
    method_sources = "\n".join(
        inspect.getsource(method)
        for method in (
            UVManager.install_packages,
            UVManager.uninstall_packages,
            UVManager.list_packages,
        )
    )

    banned_tokens = (
        'python_exe, "-m", "pip"',
        '"ensurepip"',
        "No module named pip",
        "尝试使用 uv",
        "return []",
    )
    for token in banned_tokens:
        assert token not in method_sources


@pytest.mark.asyncio
async def test_runtime_builder_passes_uv_python_version_selector(tmp_path, monkeypatch):
    calls = []

    async def fake_run_command(args, **_kwargs):
        calls.append(args)
        return CommandResult(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr("antcode_worker.runtime.builder.run_command", fake_run_command)
    builder = RuntimeBuilder(str(tmp_path / "venvs"))

    await builder._create_venv(str(tmp_path / "venv"), RuntimeSpec(python_spec=PythonSpec(version="3.11")))

    assert calls == [["uv", "venv", str(tmp_path / "venv"), "--python", "3.11"]]


@pytest.mark.asyncio
async def test_uv_manager_passes_uv_python_version_selector(tmp_path, monkeypatch):
    calls = []
    manager = UVManager(str(tmp_path / "venvs"))

    async def fake_run_command(args, **_kwargs):
        calls.append(args)
        if args[:2] == ["uv", "venv"]:
            bin_dir = tmp_path / "venvs" / "shared-py311" / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "python").touch()
            return CommandResult(exit_code=0, stdout="", stderr="")
        return CommandResult(exit_code=0, stdout="Python 3.11.11\n", stderr="")

    async def skip_package_count(_env_name):
        return None

    monkeypatch.setattr("antcode_worker.runtime.uv_manager.run_command", fake_run_command)
    monkeypatch.setattr(manager, "_update_packages_count", skip_package_count)

    created = await manager.create_env(
        "shared-py311",
        python_version="3.11",
        created_by="alice",
        owner_user_id="7",
    )

    assert calls[0] == ["uv", "venv", str(tmp_path / "venvs" / "shared-py311"), "--python", "3.11"]
    assert created["created_by"] == "alice"
    assert created["owner_user_id"] == "7"
    assert created["scope"] == "shared"
    assert created["key"] is None
    assert created["description"] is None
    assert (await manager.list_envs())[0]["owner_user_id"] == "7"
