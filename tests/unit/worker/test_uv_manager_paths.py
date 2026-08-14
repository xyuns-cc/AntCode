import json

import pytest
from antcode_contracts.runtime_metadata import RUNTIME_DESCRIPTION_MAX_LENGTH, RUNTIME_MANIFEST_MAX_BYTES
from antcode_worker.runtime.uv_manager import UVManager


def _runtime_dir(tmp_path, manifest: object):
    runtime = tmp_path / "venvs" / "private-test"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").touch()
    manifest_path = runtime / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return runtime, manifest_path


def test_uv_manager_has_no_removed_interpreter_registry():
    manager = UVManager()
    assert not hasattr(manager, "_get_interpreters_file")


@pytest.mark.parametrize(
    "env_name",
    [".", "..", "../outside", "nested/env", "/absolute", "C:\\outside"],
)
def test_uv_manager_rejects_unsafe_environment_names(tmp_path, env_name):
    manager = UVManager(str(tmp_path / "venvs"))

    with pytest.raises(ValueError, match="非法环境名"):
        manager._get_venv_path(env_name)


def test_uv_manager_rejects_symlink_that_resolves_outside_venvs(tmp_path):
    venvs_dir = tmp_path / "venvs"
    outside_dir = tmp_path / "outside"
    venvs_dir.mkdir()
    outside_dir.mkdir()
    (venvs_dir / "linked-env").symlink_to(outside_dir, target_is_directory=True)
    manager = UVManager(str(venvs_dir))

    with pytest.raises(ValueError, match="越界"):
        manager._get_venv_path("linked-env")


@pytest.mark.asyncio
async def test_uv_manager_rejects_oversized_metadata_before_writing(tmp_path):
    manager = UVManager(str(tmp_path / "venvs"))

    with pytest.raises(ValueError, match="description UTF-8 长度"):
        await manager.update_env("private-test", description="x" * (RUNTIME_DESCRIPTION_MAX_LENGTH + 1))

    assert not (tmp_path / "venvs" / "private-test").exists()


@pytest.mark.asyncio
async def test_update_env_rejects_corrupt_manifest_without_overwriting(tmp_path):
    manager = UVManager(str(tmp_path))
    env_dir = tmp_path / "private-test"
    env_dir.mkdir()
    manifest = env_dir / "manifest.json"
    manifest.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="运行时清单文件无效"):
        await manager.update_env("private-test", description="valid")

    assert manifest.read_text(encoding="utf-8") == "{broken"


@pytest.mark.asyncio
async def test_uv_manager_rejects_persisted_oversized_metadata_on_read(tmp_path):
    _runtime_dir(tmp_path, {"description": "x" * (RUNTIME_DESCRIPTION_MAX_LENGTH + 1)})
    manager = UVManager(str(tmp_path / "venvs"))

    with pytest.raises(RuntimeError, match="metadata 超出合同"):
        await manager.list_envs()


@pytest.mark.asyncio
async def test_uv_manager_rejects_manifest_over_total_byte_contract(tmp_path):
    _, manifest_path = _runtime_dir(tmp_path, {})
    manifest_path.write_bytes(b"{" + b" " * RUNTIME_MANIFEST_MAX_BYTES + b"}")
    manager = UVManager(str(tmp_path / "venvs"))

    with pytest.raises(RuntimeError, match="清单文件超过"):
        await manager.get_env("private-test")


@pytest.mark.asyncio
async def test_uv_manager_does_not_overwrite_invalid_manifest_on_update(tmp_path):
    _, manifest_path = _runtime_dir(tmp_path, ["not", "an", "object"])
    original = manifest_path.read_bytes()
    manager = UVManager(str(tmp_path / "venvs"))

    with pytest.raises(RuntimeError, match="根节点必须是 object"):
        await manager.update_env("private-test", key="valid")

    assert manifest_path.read_bytes() == original
