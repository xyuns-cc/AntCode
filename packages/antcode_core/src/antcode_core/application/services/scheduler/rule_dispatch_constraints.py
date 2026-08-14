"""Routing constraints shared by direct and Crawl batch Rule dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuleDispatchConstraints:
    region: str | None
    require_render: bool


def resolve_rule_dispatch_constraints(
    rule_detail: Any,
    serialized_rule: dict[str, Any],
) -> RuleDispatchConstraints:
    region = rule_detail.region
    if region is not None:
        if not isinstance(region, str):
            raise TypeError("规则执行区域必须是字符串或 null")
        region = region.strip() or None
    require_render = rule_detail.require_render
    if not isinstance(require_render, bool):
        raise TypeError("规则渲染约束必须是布尔值")
    return RuleDispatchConstraints(
        region=region,
        require_render=require_render or rule_payload_requires_render(serialized_rule),
    )


def rule_payload_requires_render(rule: dict[str, Any]) -> bool:
    engine = str(rule.get("engine") or "").lower()
    pagination = rule.get("pagination_config") or {}
    if not isinstance(pagination, dict):
        raise TypeError("规则 pagination_config 必须是对象")
    method = str(pagination.get("method") or "").lower()
    return engine in {"playwright", "render"} or method in {
        "js_click",
        "infinite_scroll",
        "javascript",
        "ajax",
    }


__all__ = ["RuleDispatchConstraints", "resolve_rule_dispatch_constraints", "rule_payload_requires_render"]
