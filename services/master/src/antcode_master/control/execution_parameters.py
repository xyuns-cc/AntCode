"""Run-scoped execution parameters for durable recovery runs."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

RECOVERY_CONTEXT_KEY = "recovery_context"


@dataclass(frozen=True)
class RecoveryExecutionOptions:
    source_run_id: str
    params: Mapping[str, Any]


def add_recovery_context(
    result_data: dict[str, Any],
    options: RecoveryExecutionOptions | None,
) -> dict[str, Any]:
    updated = dict(result_data)
    if options is not None:
        updated[RECOVERY_CONTEXT_KEY] = {
            "source_run_id": options.source_run_id,
            "params": dict(options.params),
        }
    return updated


def effective_execution_params(task_params: Any, run_result_data: Any) -> dict[str, Any]:
    params = dict(task_params) if isinstance(task_params, dict) else {}
    result_data = run_result_data if isinstance(run_result_data, dict) else {}
    context = result_data.get(RECOVERY_CONTEXT_KEY)
    override = context.get("params") if isinstance(context, dict) else None
    if isinstance(override, dict):
        params.update(override)
    return params


__all__ = ["RecoveryExecutionOptions", "add_recovery_context", "effective_execution_params"]
