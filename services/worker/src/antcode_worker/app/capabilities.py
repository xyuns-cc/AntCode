"""Build the capability snapshot used by registration, Lease, and heartbeat."""

from __future__ import annotations

from typing import Any

from antcode_worker.heartbeat.reporter import get_capability_detector


def detect_plugin_capabilities(plugin_registry: Any) -> dict[str, Any]:
    return get_capability_detector().detect_all(
        force_refresh=True,
        task_types=plugin_registry.capabilities(),
    )


__all__ = ["detect_plugin_capabilities"]
