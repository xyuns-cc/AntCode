"""Pure conversions between Worker heartbeat objects and Lease metrics protobufs."""

from __future__ import annotations

from typing import Any


def _value(source: Any, name: str, default: Any) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _spider_stats_dict(stats: Any) -> dict[str, Any] | None:
    if stats is None:
        return None
    return {
        "request_count": int(_value(stats, "request_count", 0) or 0),
        "response_count": int(_value(stats, "response_count", 0) or 0),
        "item_scraped_count": int(_value(stats, "item_scraped_count", 0) or 0),
        "error_count": int(_value(stats, "error_count", 0) or 0),
        "avg_latency_ms": float(_value(stats, "avg_latency_ms", 0.0) or 0.0),
        "requests_per_minute": float(_value(stats, "requests_per_minute", 0.0) or 0.0),
        "status_codes": dict(_value(stats, "status_codes", {}) or {}),
        "domain_stats": list(_value(stats, "domain_stats", []) or []),
    }


def heartbeat_metrics_dict(heartbeat: Any) -> dict[str, Any]:
    metrics = getattr(heartbeat, "metrics", None)
    if metrics is None:
        return {
            "cpu": getattr(heartbeat, "cpu_percent", 0.0),
            "memory": getattr(heartbeat, "memory_percent", 0.0),
            "disk": getattr(heartbeat, "disk_percent", 0.0),
            "running_tasks": getattr(heartbeat, "running_tasks", 0),
            "max_concurrent_tasks": getattr(heartbeat, "max_concurrent_tasks", 5),
        }
    return {
        "cpu": _value(metrics, "cpu", 0.0),
        "memory": _value(metrics, "memory", 0.0),
        "disk": _value(metrics, "disk", 0.0),
        "running_tasks": _value(metrics, "running_tasks", 0),
        "max_concurrent_tasks": _value(metrics, "max_concurrent_tasks", 5),
        "task_count": _value(metrics, "task_count", 0),
        "project_count": _value(metrics, "project_count", 0),
        "env_count": _value(metrics, "env_count", 0),
        "queued_tasks": _value(metrics, "queued_tasks", 0),
        "cpu_cores": _value(metrics, "cpu_cores", 0),
        "memory_total_bytes": _value(metrics, "memory_total_bytes", 0),
        "memory_used_bytes": _value(metrics, "memory_used_bytes", 0),
        "memory_available_bytes": _value(metrics, "memory_available_bytes", 0),
        "disk_total_bytes": _value(metrics, "disk_total_bytes", 0),
        "disk_used_bytes": _value(metrics, "disk_used_bytes", 0),
        "disk_free_bytes": _value(metrics, "disk_free_bytes", 0),
        "uptime_seconds": _value(metrics, "uptime_seconds", 0),
        "spider_stats": _spider_stats_dict(_value(metrics, "spider_stats", None)),
    }


def _domain_proto(value: Any) -> Any:
    from antcode_contracts import common_pb2

    return common_pb2.SpiderDomainStats(
        domain=str(_value(value, "domain", "") or ""),
        request_count=int(_value(value, "reqs", 0) or 0),
        success_rate=float(_value(value, "successRate", 0.0) or 0.0),
        avg_latency_ms=float(_value(value, "latency", 0.0) or 0.0),
    )


def _spider_stats_proto(value: Any) -> Any | None:
    if not value:
        return None
    from antcode_contracts import common_pb2

    status_codes = {int(code): int(count) for code, count in _value(value, "status_codes", {}).items()}
    domains = [_domain_proto(item) for item in _value(value, "domain_stats", [])]
    return common_pb2.SpiderStatsSummary(
        request_count=int(_value(value, "request_count", 0) or 0),
        response_count=int(_value(value, "response_count", 0) or 0),
        item_scraped_count=int(_value(value, "item_scraped_count", 0) or 0),
        error_count=int(_value(value, "error_count", 0) or 0),
        avg_latency_ms=float(_value(value, "avg_latency_ms", 0.0) or 0.0),
        requests_per_minute=float(_value(value, "requests_per_minute", 0.0) or 0.0),
        status_codes=status_codes,
        domain_stats=domains,
    )


def build_metrics_proto(metrics: dict[str, Any]) -> Any:
    from antcode_contracts import common_pb2

    values: dict[str, Any] = {
        "cpu": float(metrics.get("cpu", 0.0) or 0.0),
        "memory": float(metrics.get("memory", 0.0) or 0.0),
        "disk": float(metrics.get("disk", 0.0) or 0.0),
        "running_tasks": int(metrics.get("running_tasks", 0) or 0),
        "max_concurrent_tasks": int(metrics.get("max_concurrent_tasks", 5) or 5),
        "task_count": int(metrics.get("task_count", 0) or 0),
        "project_count": int(metrics.get("project_count", 0) or 0),
        "env_count": int(metrics.get("env_count", 0) or 0),
        "queued_tasks": int(metrics.get("queued_tasks", 0) or 0),
        "cpu_cores": int(metrics.get("cpu_cores", 0) or 0),
        "memory_total_bytes": int(metrics.get("memory_total_bytes", 0) or 0),
        "memory_used_bytes": int(metrics.get("memory_used_bytes", 0) or 0),
        "memory_available_bytes": int(metrics.get("memory_available_bytes", 0) or 0),
        "disk_total_bytes": int(metrics.get("disk_total_bytes", 0) or 0),
        "disk_used_bytes": int(metrics.get("disk_used_bytes", 0) or 0),
        "disk_free_bytes": int(metrics.get("disk_free_bytes", 0) or 0),
        "uptime_seconds": int(metrics.get("uptime_seconds", 0) or 0),
    }
    spider_stats = _spider_stats_proto(metrics.get("spider_stats"))
    if spider_stats is not None:
        values["spider_stats"] = spider_stats
    return common_pb2.Metrics(**values)


__all__ = ["build_metrics_proto", "heartbeat_metrics_dict"]
