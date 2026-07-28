from __future__ import annotations

import builtins
import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path("scripts/run_worker.py").resolve()


def test_wrapper_rejects_invalid_worker_cli_arguments(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "run", "--transport", "invalid"])

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert exc_info.value.code == 2


def test_wrapper_reports_missing_worker_dependency(monkeypatch) -> None:
    original_import = builtins.__import__

    def reject_worker_cli(name, *args, **kwargs):
        if name == "antcode_worker.cli":
            raise ImportError("missing worker package")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_worker_cli)

    with pytest.raises(SystemExit, match="uv sync"):
        runpy.run_path(str(SCRIPT), run_name="run_worker_import_test")


def test_wrapper_delegates_arguments_and_propagates_cli_failure(monkeypatch) -> None:
    cli_module = ModuleType("antcode_worker.cli")
    observed_argv: list[str] = []

    def main() -> None:
        observed_argv.extend(sys.argv)
        raise RuntimeError("worker startup failed")

    cli_module.main = main
    monkeypatch.setitem(sys.modules, "antcode_worker.cli", cli_module)
    argv = [str(SCRIPT), "run", "--name", "Worker-E2E", "--port", "8001"]
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(RuntimeError, match="worker startup failed"):
        runpy.run_path(str(SCRIPT), run_name="__main__")

    assert observed_argv == argv
