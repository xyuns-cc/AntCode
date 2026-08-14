"""Immutable context and capability checks for scheduler Worker selection."""

from dataclasses import dataclass
from typing import Any

from antcode_core.application.services.scheduler.rule_dispatch_constraints import (
    RuleDispatchConstraints,
)
from antcode_core.application.services.workers.worker_capability_routing import (
    has_render_capability,
    resolve_selection_capabilities,
    supports_task_types,
)
from antcode_core.common.exceptions import WorkerUnavailableError
from antcode_core.domain.models.enums import ExecutionStrategy

RULE_TASK_TYPE = "rule"
NO_RULE_DISPATCH_CONSTRAINTS = RuleDispatchConstraints(region=None, require_render=False)


@dataclass(frozen=True)
class ResolutionContext:
    task: Any
    project: Any
    strategy: ExecutionStrategy
    constraints: RuleDispatchConstraints
    required_task_type: str | None


def required_task_type(project: Any) -> str | None:
    project_type = getattr(project, "type", None)
    value = getattr(project_type, "value", project_type)
    return RULE_TASK_TYPE if value == RULE_TASK_TYPE else None


async def require_worker_constraints(
    worker: Any,
    *,
    constraints: RuleDispatchConstraints,
    required_type: str | None,
) -> None:
    if constraints.region and worker.region != constraints.region:
        raise WorkerUnavailableError(
            f"Worker [{worker.name}] 区域不匹配: required={constraints.region}, actual={worker.region}",
            str(worker.id),
        )
    if not constraints.require_render and not required_type:
        return
    capabilities = await resolve_selection_capabilities(
        [worker],
        constraints.require_render,
        required_type,
    )
    worker_capabilities = capabilities.get(worker.id, {})
    if constraints.require_render and not has_render_capability(worker_capabilities):
        raise WorkerUnavailableError(f"Worker [{worker.name}] 不具备渲染能力", str(worker.id))
    if required_type and not supports_task_types(worker_capabilities, required_type):
        raise WorkerUnavailableError(
            f"Worker [{worker.name}] 不支持任务类型 {required_type}",
            str(worker.id),
        )


__all__ = [
    "NO_RULE_DISPATCH_CONSTRAINTS",
    "ResolutionContext",
    "require_worker_constraints",
    "required_task_type",
]
