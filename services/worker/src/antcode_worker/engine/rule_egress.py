"""Rule execution wrapper with a Worker-owned SSRF-safe egress proxy."""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path

from antcode_core.application.services.projects.git_transfer_quota import DurationBudget, TransferBudget
from antcode_core.application.services.projects.pinned_http_proxy import restricted_http_proxy

from antcode_worker.domain.models import ExecPlan
from antcode_worker.engine.rule_egress_bridge import unix_proxy_bridge
from antcode_worker.executor.rule_policy import (
    RULE_EGRESS_BYTE_BUDGET_CONFIG,
    RULE_EGRESS_CONNECTION_LIMIT_CONFIG,
    RULE_EGRESS_DURATION_CONFIG,
    RULE_EGRESS_LOOPBACK_PORT,
    RULE_EGRESS_PROXY_ENV,
    RULE_EGRESS_SOCKET_CONFIG,
)
from antcode_worker.rule_egress_limits import RuleEgressLimits


def _bridge_work_dir(plan: ExecPlan) -> str:
    spool_path = plan.env.get("ANTCODE_SPIDER_SPOOL_PATH", "")
    if not spool_path:
        raise RuntimeError("Rule egress 缺少可信 spool 路径")
    return str(Path(spool_path).parent)


@contextlib.contextmanager
def rule_egress_plan(plan: ExecPlan, limits: RuleEgressLimits, *, default_max_processes: int):
    if plan.plugin_name != "rule":
        yield plan
        return
    task_limits = limits.constrain_for_task(
        process_limit=_process_limit(plan, default_max_processes),
        timeout_seconds=plan.timeout_seconds,
    )
    budget = TransferBudget(task_limits.max_bytes, label="Rule egress 累计流量")
    duration_budget = DurationBudget(task_limits.max_duration_seconds, label="Rule egress")
    with restricted_http_proxy(
        budget=budget,
        max_connections=task_limits.max_connections,
        duration_budget=duration_budget,
    ) as proxy_url:
        with unix_proxy_bridge(
            proxy_url,
            _bridge_work_dir(plan),
            max_connections=task_limits.max_connections,
            max_duration_seconds=task_limits.max_duration_seconds,
        ) as socket_path:
            env = {
                **plan.env,
                RULE_EGRESS_PROXY_ENV: f"http://127.0.0.1:{RULE_EGRESS_LOOPBACK_PORT}",
            }
            sandbox_config = {
                **plan.sandbox_config,
                RULE_EGRESS_SOCKET_CONFIG: socket_path,
                RULE_EGRESS_CONNECTION_LIMIT_CONFIG: task_limits.max_connections,
                RULE_EGRESS_BYTE_BUDGET_CONFIG: task_limits.max_bytes,
                RULE_EGRESS_DURATION_CONFIG: task_limits.max_duration_seconds,
            }
            yield replace(plan, env=env, sandbox_config=sandbox_config)


def _process_limit(plan: ExecPlan, default_max_processes: int) -> int:
    value = plan.max_processes or default_max_processes
    if type(value) is not int or value <= 0:
        raise RuntimeError("Rule egress 连接上限必须来自有效的任务进程上限")
    return value


__all__ = ["rule_egress_plan"]
