"""Rule execution wrapper with a Worker-owned SSRF-safe egress proxy."""

from __future__ import annotations

import contextlib
from dataclasses import replace

from antcode_core.application.services.projects.pinned_http_proxy import restricted_http_proxy

from antcode_worker.domain.models import ExecPlan


@contextlib.contextmanager
def rule_egress_plan(plan: ExecPlan):
    if plan.plugin_name != "rule":
        yield plan
        return
    with restricted_http_proxy() as proxy_url:
        env = {**plan.env, "ANTCODE_SPIDER_EGRESS_PROXY": proxy_url}
        yield replace(plan, env=env)


__all__ = ["rule_egress_plan"]
