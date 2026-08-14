"""Trusted, operator-configurable resource budgets for one Rule task."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

DEFAULT_RULE_EGRESS_MAX_CONNECTIONS = 32
DEFAULT_RULE_EGRESS_MAX_BYTES = 1024 * 1024 * 1024
DEFAULT_RULE_EGRESS_MAX_DURATION_SECONDS = 3600


@dataclass(frozen=True)
class RuleEgressLimits:
    max_connections: int = DEFAULT_RULE_EGRESS_MAX_CONNECTIONS
    max_bytes: int = DEFAULT_RULE_EGRESS_MAX_BYTES
    max_duration_seconds: int = DEFAULT_RULE_EGRESS_MAX_DURATION_SECONDS

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"Rule egress {name} 必须是正整数")

    def constrain_for_task(self, *, process_limit: int, timeout_seconds: int) -> RuleEgressLimits:
        if type(process_limit) is not int or process_limit <= 0:
            raise ValueError("Rule egress 任务进程上限必须是正整数")
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("Rule egress 任务超时必须是正整数")
        return replace(
            self,
            max_connections=min(self.max_connections, process_limit),
            max_duration_seconds=min(self.max_duration_seconds, timeout_seconds),
        )


def load_rule_egress_env(get_int: Callable[[str], int | None]) -> dict[str, int]:
    mappings = {
        "rule_egress_max_connections": "WORKER_RULE_EGRESS_MAX_CONNECTIONS",
        "rule_egress_max_bytes": "WORKER_RULE_EGRESS_MAX_BYTES",
        "rule_egress_max_duration_seconds": "WORKER_RULE_EGRESS_MAX_DURATION_SECONDS",
    }
    values = ((field, get_int(env_name)) for field, env_name in mappings.items())
    return {field: value for field, value in values if value is not None}


def limits_from_config(config: Any) -> RuleEgressLimits:
    return RuleEgressLimits(
        max_connections=getattr(config, "rule_egress_max_connections", DEFAULT_RULE_EGRESS_MAX_CONNECTIONS),
        max_bytes=getattr(config, "rule_egress_max_bytes", DEFAULT_RULE_EGRESS_MAX_BYTES),
        max_duration_seconds=getattr(
            config,
            "rule_egress_max_duration_seconds",
            DEFAULT_RULE_EGRESS_MAX_DURATION_SECONDS,
        ),
    )


__all__ = ["RuleEgressLimits", "limits_from_config", "load_rule_egress_env"]
