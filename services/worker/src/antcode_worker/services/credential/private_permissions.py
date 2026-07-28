"""Owner-only file permissions for Worker-local secrets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ACL_COMMAND_TIMEOUT_SECONDS = 5


def set_owner_only_permissions(path: Path, mode: int) -> None:
    if os.name != "nt":
        os.chmod(path, mode)
        return
    identity = subprocess.run(
        ["whoami"],
        check=True,
        capture_output=True,
        text=True,
        timeout=ACL_COMMAND_TIMEOUT_SECONDS,
    ).stdout.strip()
    if not identity:
        raise PermissionError("无法确定 Windows Worker 凭证文件所有者")
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{identity}:(F)"],
        check=False,
        capture_output=True,
        text=True,
        timeout=ACL_COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise PermissionError(f"Windows Worker 凭证 ACL 设置失败: {result.stderr.strip()}")


__all__ = ["set_owner_only_permissions"]
