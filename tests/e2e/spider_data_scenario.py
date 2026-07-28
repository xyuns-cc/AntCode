"""Real rule-crawler E2E provisioning and strict cleanup."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from .conftest import E2EConfig
from .helpers import create_task, ensure_shared_env, extract_data, request_json
from .run_scenarios import TERMINAL_STATUSES, list_task_runs

CRAWL_TARGET_URL = "https://example.com/"
EXPECTED_TITLE = "Example Domain"
SPIDER_NAME = "antcode_rule"
SPIDER_ITEM_PAGE_SIZE = 100


@dataclass(frozen=True)
class SpiderDataResources:
    project: dict[str, Any]
    task: dict[str, Any]


def build_rule_project_form(config: E2EConfig, worker_id: str) -> dict[str, str]:
    extraction_rules = [{"desc": "title", "type": "css", "expr": "h1::text"}]
    return {
        "name": f"e2e-rule-{uuid.uuid4().hex[:8]}",
        "type": "rule",
        "runtime_scope": "shared",
        "python_version": config.runtime_python_version,
        "use_existing_env": "true",
        "existing_env_name": config.shared_env_name,
        "worker_id": worker_id,
        "engine": "requests",
        "target_url": CRAWL_TARGET_URL,
        "callback_type": "list",
        "extraction_rules": json.dumps(extraction_rules),
        "max_pages": "1",
        "request_delay": "0",
        "retry_count": "0",
        "timeout": "30",
    }


async def create_rule_project(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
    *,
    config: E2EConfig,
) -> dict[str, Any]:
    form = build_rule_project_form(config, worker_id)
    payload = await request_json(client, "POST", "/projects", token=token, data=form)
    project = extract_data(payload) or {}
    assert project.get("id"), "规则项目创建失败"
    return project


async def list_spider_items(
    client: httpx.AsyncClient,
    token: str,
    run_id: str,
) -> list[dict[str, Any]]:
    payload = await request_json(
        client,
        "GET",
        f"/runs/{run_id}/spider-items",
        token=token,
        params={"start_id": "0", "count": SPIDER_ITEM_PAGE_SIZE},
    )
    data = extract_data(payload)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise AssertionError(f"Spider items 响应格式错误: {data!r}")
    return data["items"]


def assert_expected_spider_item(
    items: Sequence[dict[str, Any]],
    *,
    run_id: str,
    project_id: str,
) -> dict[str, Any]:
    matches = [item for item in items if _item_title(item) == EXPECTED_TITLE]
    assert matches, f"未找到预期抓取数据: items={items!r}"
    item = matches[0]
    assert item.get("run_id") == run_id
    assert item.get("project_id") == project_id
    assert item.get("spider_name") == SPIDER_NAME
    assert int(item.get("sequence", 0)) >= 1
    return item


def _item_title(item: dict[str, Any]) -> Any:
    data = item.get("data")
    return data.get("title") if isinstance(data, dict) else None


async def _delete_spider_storage(run_ids: Sequence[str], project_id: str) -> None:
    if not run_ids:
        return
    from antcode_core.application.services.crawl.spider_storage_cleanup import (
        SpiderStorageCleanupService,
    )
    from antcode_core.common.config import settings
    from antcode_core.infrastructure.redis import get_redis_client
    from antcode_core.infrastructure.redis.keys import RedisKeys

    redis = await get_redis_client()
    if redis is None:
        raise RuntimeError("E2E Spider 数据清理失败: Redis client unavailable")
    keys = RedisKeys(namespace=settings.REDIS_NAMESPACE)
    await SpiderStorageCleanupService(redis, keys).delete_runs(run_ids, project_id)
    await _assert_spider_storage_deleted(
        redis,
        keys,
        run_ids=run_ids,
        project_id=project_id,
    )


async def _assert_spider_storage_deleted(
    redis,
    keys,
    *,
    run_ids: Sequence[str],
    project_id: str,
) -> None:
    storage_keys = []
    for run_id in run_ids:
        storage_keys.extend(
            [
                keys.spider_data_stream(run_id),
                keys.spider_meta_key(run_id),
                keys.spider_item_ids_key(run_id),
                keys.spider_item_order_key(run_id),
            ]
        )
    remaining = await redis.exists(*storage_keys)
    if remaining:
        raise AssertionError(f"E2E Spider 数据清理不完整: remaining_keys={remaining}")
    for run_id in run_ids:
        if await redis.zscore(keys.spider_index_key(project_id), run_id) is not None:
            raise AssertionError(f"E2E Spider 索引清理不完整: run_id={run_id}")


async def _cleanup_resources(
    client: httpx.AsyncClient,
    token: str,
    *,
    project: dict[str, Any] | None,
    task: dict[str, Any] | None,
    config: E2EConfig,
) -> list[Exception]:
    errors: list[Exception] = []
    runs = await _capture_runs(client, token, task, errors=errors)
    quiesced = await _quiesce_runs(
        client,
        token,
        task,
        runs=runs,
        config=config,
        errors=errors,
    )
    run_ids = [str(run["run_id"]) for run in runs if run.get("run_id")]
    if quiesced and project is not None:
        await _capture_cleanup(_delete_spider_storage(run_ids, project["id"]), errors)
    for path in _cleanup_paths(task, project):
        await _capture_cleanup(request_json(client, "DELETE", path, token=token), errors)
    return errors


async def _capture_runs(
    client: httpx.AsyncClient,
    token: str,
    task: dict[str, Any] | None,
    *,
    errors: list[Exception],
) -> list[dict[str, Any]]:
    if task is None:
        return []
    try:
        runs = await list_task_runs(client, token, task["id"])
        return runs
    except Exception as exc:
        errors.append(exc)
        return []


async def _quiesce_runs(
    client: httpx.AsyncClient,
    token: str,
    task: dict[str, Any] | None,
    *,
    runs: Sequence[dict[str, Any]],
    config: E2EConfig,
    errors: list[Exception],
) -> bool:
    if task is None:
        return True
    active = [run for run in runs if str(run.get("status")) not in TERMINAL_STATUSES]
    for run in active:
        await _capture_cleanup(
            request_json(client, "POST", f"/runs/{run['run_id']}/cancel", token=token),
            errors,
        )
    if not active:
        return True
    try:
        await _wait_runs_terminal(client, token, task["id"], config=config)
        return True
    except Exception as exc:
        errors.append(exc)
        return False


async def _wait_runs_terminal(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
    *,
    config: E2EConfig,
) -> None:
    deadline = time.monotonic() + config.poll_timeout
    while time.monotonic() < deadline:
        runs = await list_task_runs(client, token, task_id)
        if all(str(run.get("status")) in TERMINAL_STATUSES for run in runs):
            return
        await asyncio.sleep(config.poll_interval)
    raise AssertionError("等待 Spider E2E 执行终态超时")


async def _capture_cleanup(operation, errors: list[Exception]) -> None:
    try:
        await operation
    except Exception as exc:
        errors.append(exc)


def _cleanup_paths(
    task: dict[str, Any] | None,
    project: dict[str, Any] | None,
) -> list[str]:
    paths = [f"/tasks/{task['id']}"] if task is not None else []
    if project is not None:
        paths.append(f"/projects/{project['id']}")
    return paths


@asynccontextmanager
async def provision_spider_data_scenario(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
    *,
    config: E2EConfig,
) -> AsyncIterator[SpiderDataResources]:
    project = None
    task = None
    failure: BaseException | None = None
    try:
        await ensure_shared_env(client, token, worker_id, config=config)
        project = await create_rule_project(client, token, worker_id, config=config)
        task = await create_task(client, token, project["id"], worker_id=worker_id)
        yield SpiderDataResources(project=project, task=task)
    except BaseException as exc:
        failure = exc
    cleanup_errors = await _cleanup_resources(
        client,
        token,
        project=project,
        task=task,
        config=config,
    )
    if failure is not None and cleanup_errors:
        raise BaseExceptionGroup("Spider E2E 与资源清理均失败", [failure, *cleanup_errors])
    if failure is not None:
        raise failure
    if cleanup_errors:
        raise ExceptionGroup("Spider E2E 资源清理失败", cleanup_errors)
