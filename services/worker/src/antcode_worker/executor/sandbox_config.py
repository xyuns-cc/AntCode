"""Configuration for Worker task sandboxes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from antcode_worker.config import DATA_ROOT


@dataclass
class SandboxConfig:
    """Worker sandbox policy and resource settings."""

    enabled: bool = True
    network_isolated: bool = False
    fs_isolated: bool = True
    allowed_env_vars: list[str] = field(
        default_factory=lambda: [
            "PATH",
            "HOME",
            "PYTHONPATH",
            "LANG",
            "LC_ALL",
            "VIRTUAL_ENV",
            "UV_CACHE_DIR",
            "GOCACHE",
            "GOMODCACHE",
            "GOENV",
            "GOTOOLCHAIN",
            "GOWORK",
            "GOPROXY",
            "GOFLAGS",
        ]
    )
    data_dir: str = field(default_factory=lambda: str(DATA_ROOT))
    runtimes_dir: str | None = None
    temp_dir: str | None = None
    max_file_size: int = 100 * 1024 * 1024
    max_output_size: int = 10 * 1024 * 1024
    cleanup_on_exit: bool = True
    sandbox_command: list[str] | None = None

    @classmethod
    def for_worker(
        cls,
        worker_config: Any,
        *,
        network_isolated: bool,
        sandbox_command: list[str],
    ) -> SandboxConfig:
        """Resolve authoritative Worker storage roots for task isolation."""
        data_dir = str(getattr(worker_config, "data_dir", DATA_ROOT) or DATA_ROOT)
        runtimes_dir = str(getattr(worker_config, "venvs_dir", "") or os.path.join(data_dir, "runtimes"))
        return cls(
            network_isolated=network_isolated,
            sandbox_command=sandbox_command,
            data_dir=data_dir,
            runtimes_dir=runtimes_dir,
        )


__all__ = ["SandboxConfig"]
