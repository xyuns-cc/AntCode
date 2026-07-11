from antcode_worker.runtime.uv_manager import UVManager


def test_uv_manager_has_no_removed_interpreter_registry():
    manager = UVManager()
    assert not hasattr(manager, "_get_interpreters_file")
