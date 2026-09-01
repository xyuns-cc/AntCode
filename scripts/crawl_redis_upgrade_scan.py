"""Read-only inventory and fail-closed checks for the Crawl Redis fresh-deploy gate."""

from __future__ import annotations

from typing import Any

from scripts.crawl_redis_upgrade_contract import Blocker, UpgradeReport, UpgradeRequest
from scripts.crawl_redis_upgrade_execution import execution_inventory

SCAN_COUNT = 500


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


async def _scan_keys(client: Any, pattern: str) -> tuple[str, ...]:
    keys = {_text(key) async for key in client.scan_iter(match=pattern, count=SCAN_COUNT)}
    return tuple(sorted(keys))


async def _fresh_deploy_blockers(client: Any, request: UpgradeRequest, legacy_keys: set[str]) -> list[Blocker]:
    blockers = []
    if legacy_keys:
        blockers.append(Blocker("fresh_deploy_has_legacy_data", "rule:*/crawl:*", f"keys={len(legacy_keys)}"))
    tagged_pattern = f"{{{request.namespace}:crawl:*}}:*"
    runtime_pattern = f"{request.namespace}:crawl:*"
    current = set(await _scan_keys(client, tagged_pattern))
    current.update(await _scan_keys(client, runtime_pattern))
    if current:
        detail = f"keys={len(current)}; patterns={tagged_pattern},{runtime_pattern}"
        blockers.append(Blocker("fresh_deploy_has_current_data", request.namespace, detail))
    return blockers


async def build_report(client: Any, request: UpgradeRequest) -> UpgradeReport:
    request.validate()
    legacy_keys = set(await _scan_keys(client, "rule:*"))
    legacy_keys.update(await _scan_keys(client, "crawl:*"))
    streams, stores, blockers = await execution_inventory(client, request.namespace)
    blockers.extend(await _fresh_deploy_blockers(client, request, legacy_keys))
    return UpgradeReport(
        request.namespace,
        tuple(sorted(legacy_keys)),
        tuple(streams),
        tuple(stores),
        tuple(blockers),
    )


__all__ = ["build_report"]
