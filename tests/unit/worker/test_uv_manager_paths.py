import pytest
from antcode_worker.runtime.uv_manager import UVManager


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
