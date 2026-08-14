"""Fail-closed bwrap policy for the Rule network namespace bridge."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any

from antcode_worker.executor.rule_policy import (
    RULE_EGRESS_DURATION_CONFIG,
    RULE_EGRESS_SOCKET_CONFIG,
)

_RELAY_MODULE = "antcode_worker.executor.rule_network_relay"
_PAYLOAD_PROCESS_LIMIT_CONFIG = "payload_max_processes"


def rule_bridge_socket(exec_plan: Any) -> str:
    if exec_plan.plugin_name != "rule":
        return ""
    if exec_plan.sandbox_config.get("allow_network") is True:
        raise RuntimeError("Rule 禁止共享 Worker network namespace")
    value = exec_plan.sandbox_config.get(RULE_EGRESS_SOCKET_CONFIG, "")
    if not isinstance(value, str) or not value:
        raise RuntimeError("Rule 缺少受限 egress bridge socket")
    return value


def bridge_mount_args(context: dict[str, Any]) -> tuple[str, ...]:
    socket_path = _validated_socket_path(context)
    if socket_path is None:
        return ()
    directory = str(socket_path.parent)
    return "--ro-bind", directory, directory


def wrap_rule_command(cmd: list[str], context: dict[str, Any]) -> list[str]:
    socket_path = _validated_socket_path(context)
    if socket_path is None:
        return cmd
    return [
        sys.executable,
        "-m",
        _RELAY_MODULE,
        "--socket",
        str(socket_path),
        "--max-connections",
        str(_connection_limit(context)),
        "--max-duration",
        str(_duration_limit(context)),
        "--",
        *cmd,
    ]


def _connection_limit(context: dict[str, Any]) -> int:
    value = context.get(_PAYLOAD_PROCESS_LIMIT_CONFIG)
    if type(value) is not int or value <= 0:
        raise RuntimeError("Rule egress bridge 缺少有效连接上限")
    return value


def _duration_limit(context: dict[str, Any]) -> int:
    value = context.get(RULE_EGRESS_DURATION_CONFIG)
    if type(value) is not int or value <= 0:
        raise RuntimeError("Rule egress bridge 缺少有效总时长上限")
    return value


def _validated_socket_path(context: dict[str, Any]) -> Path | None:
    raw_path = context.get(RULE_EGRESS_SOCKET_CONFIG, "")
    if not raw_path:
        return None
    if not isinstance(raw_path, str):
        raise RuntimeError("Rule egress bridge socket 路径类型无效")
    socket_path = Path(raw_path)
    if not socket_path.is_absolute() or socket_path.is_symlink():
        raise RuntimeError("Rule egress bridge socket 必须是非符号链接绝对路径")
    _validate_owned_node(socket_path.parent, directory=True, expected_mode=0o700)
    _validate_owned_node(socket_path, directory=False, expected_mode=0o600)
    return socket_path


def _validate_owned_node(path: Path, *, directory: bool, expected_mode: int) -> None:
    node_stat = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISSOCK
    if path.is_symlink() or not expected_type(node_stat.st_mode):
        kind = "目录" if directory else "socket"
        raise RuntimeError(f"Rule egress bridge {kind} 类型无效")
    if node_stat.st_uid != os.geteuid():
        raise RuntimeError("Rule egress bridge 必须由 Worker 运行用户持有")
    if stat.S_IMODE(node_stat.st_mode) != expected_mode:
        raise RuntimeError(f"Rule egress bridge 权限必须是 {expected_mode:04o}")


__all__ = ["bridge_mount_args", "rule_bridge_socket", "wrap_rule_command"]
