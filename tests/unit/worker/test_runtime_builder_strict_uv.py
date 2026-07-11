import inspect

from antcode_worker.runtime.builder import RuntimeBuilder
from antcode_worker.runtime.uv_manager import UVManager


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
