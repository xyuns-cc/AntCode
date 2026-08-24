"""Linux bubblewrap integration for the per-task filesystem view."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig

_BWRAP_TIMEOUT_SECONDS = 10


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="需要 Linux bubblewrap")
def test_real_bwrap_cannot_read_sibling_workspace(tmp_path: Path) -> None:
    work_dir = tmp_path / "runs" / "sources" / "current" / "project"
    sibling_secret = tmp_path / "runs" / "sources" / "other-tenant" / "project" / "secret.txt"
    work_dir.mkdir(parents=True)
    sibling_secret.parent.mkdir(parents=True)
    sibling_secret.write_text("must-not-be-visible", encoding="utf-8")
    sandbox = BasicSandbox(
        SandboxConfig(
            network_isolated=True,
            sandbox_command=[shutil.which("bwrap") or ""],
            data_dir=str(tmp_path),
        )
    )
    context = {
        "work_dir": str(work_dir),
        "plugin_name": "code",
        "run_id": "current",
        "runtime_executable": sys.executable,
        "tmpfs_size_mb": 512,
    }
    probe = "from pathlib import Path; import sys; raise SystemExit(97 if Path(sys.argv[1]).exists() else 0)"

    command = sandbox.wrap_command([sys.executable, "-c", probe, str(sibling_secret)], context)
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=_BWRAP_TIMEOUT_SECONDS)

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None, reason="需要 Linux bubblewrap")
def test_real_bwrap_provides_private_shared_memory(tmp_path: Path) -> None:
    work_dir = tmp_path / "runs" / "sources" / "current" / "project"
    work_dir.mkdir(parents=True)
    sandbox = BasicSandbox(
        SandboxConfig(
            network_isolated=True,
            sandbox_command=[shutil.which("bwrap") or ""],
            data_dir=str(tmp_path),
        )
    )
    context = {
        "work_dir": str(work_dir),
        "plugin_name": "code",
        "run_id": "current",
        "runtime_executable": sys.executable,
        "tmpfs_size_mb": 512,
    }
    probe = (
        "import os, stat; mode=stat.S_IMODE(os.stat('/dev/shm').st_mode); raise SystemExit(0 if mode == 0o1777 else 98)"
    )

    command = sandbox.wrap_command([sys.executable, "-c", probe], context)
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=_BWRAP_TIMEOUT_SECONDS)

    assert result.returncode == 0, result.stderr
