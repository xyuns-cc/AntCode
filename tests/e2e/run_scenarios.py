"""Reusable real-service E2E task scenarios with strict cleanup."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from .conftest import E2EConfig
from .helpers import (
    CodeProjectResources,
    create_code_project,
    create_task,
    ensure_shared_env,
    extract_data,
    request_json,
    trigger_task,
)
from .source_repository import E2EGitSource, publish_e2e_git_source

TERMINAL_STATUSES = frozenset({"success", "failed", "cancelled", "timeout"})
DEFAULT_TASK_TIMEOUT_SECONDS = 120
DEFAULT_RETRY_DELAY_SECONDS = 1
RUN_PAGE_SIZE = 20


@dataclass(frozen=True)
class RunScenario:
    expected_status: str
    log_delay_seconds: float = 0
    script_delay_seconds: float = 0
    exit_code: int = 0
    timeout_seconds: int = DEFAULT_TASK_TIMEOUT_SECONDS
    retry_count: int = 0
    retry_delay: int = DEFAULT_RETRY_DELAY_SECONDS

    def __post_init__(self) -> None:
        if self.expected_status not in TERMINAL_STATUSES:
            raise ValueError(f"不支持的终态: {self.expected_status}")
        if self.log_delay_seconds < 0 or self.script_delay_seconds < 0 or self.timeout_seconds <= 0:
            raise ValueError("场景延迟不能为负数，任务超时必须为正数")
        if self.retry_count < 0 or self.retry_delay <= 0:
            raise ValueError("重试次数不能为负数，重试延迟必须为正数")


@dataclass(frozen=True)
class ScenarioResources:
    source: E2EGitSource
    code_project: CodeProjectResources
    task: dict[str, Any]
    scenario: RunScenario

    @property
    def project(self) -> dict[str, Any]:
        return self.code_project.project

    @property
    def repository(self) -> dict[str, Any]:
        return self.code_project.repository


def render_scenario_source(log_token: str, scenario: RunScenario) -> str:
    log_delay = f"time.sleep({scenario.log_delay_seconds!r})\n" if scenario.log_delay_seconds else ""
    return (
        "import logging\n"
        "import time\n"
        "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "logger = logging.getLogger(__name__)\n"
        f"{log_delay}"
        f"logger.info({log_token!r})\n"
        f"time.sleep({scenario.script_delay_seconds!r})\n"
        f"raise SystemExit({scenario.exit_code})\n"
    )


async def _cleanup_created(
    client: httpx.AsyncClient,
    token: str,
    *,
    source: E2EGitSource,
    code_project: CodeProjectResources | None,
    task: dict[str, Any] | None,
) -> list[Exception]:
    errors: list[Exception] = []
    paths = []
    if task is not None:
        paths.append(f"/tasks/{task['id']}")
    if code_project is not None:
        paths.extend(
            [
                f"/projects/{code_project.project['id']}",
                f"/repositories/{code_project.repository['id']}",
            ]
        )
    for path in paths:
        try:
            await _delete_when_no_longer_in_use(client, token, path)
        except Exception as exc:
            errors.append(exc)
    try:
        source.cleanup()
    except Exception as exc:
        errors.append(exc)
    return errors


CLEANUP_CONFLICT_ATTEMPTS = 30
CLEANUP_CONFLICT_INTERVAL_SECONDS = 2.0


async def _delete_when_no_longer_in_use(client: httpx.AsyncClient, token: str, path: str) -> None:
    """删除资源，遇 409 就退避重试。

    "运行中的任务/项目不可删" 是产品的正确行为，而用例读完日志时任务往往还没
    落终态。清理阶段直接 DELETE 会拿到 409 并把整条用例判失败——失败的是拆卸而
    不是被测行为。这里等它进入可删状态，超时仍冲突才抛出。
    """
    last_error: Exception | None = None
    for _ in range(CLEANUP_CONFLICT_ATTEMPTS):
        try:
            await request_json(client, "DELETE", path, token=token)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != httpx.codes.CONFLICT:
                raise
            last_error = exc
            await asyncio.sleep(CLEANUP_CONFLICT_INTERVAL_SECONDS)
    if last_error is not None:
        raise last_error


@asynccontextmanager
async def provision_scenario(
    client: httpx.AsyncClient,
    token: str,
    worker_id: str,
    *,
    config: E2EConfig,
    scenario: RunScenario,
) -> AsyncIterator[ScenarioResources]:
    log_token = f"E2E-{scenario.expected_status.upper()}-{uuid.uuid4().hex[:8]}"
    source = publish_e2e_git_source(log_token, source_code=render_scenario_source(log_token, scenario))
    code_project = None
    task = None
    failure: BaseException | None = None
    try:
        await ensure_shared_env(client, token, worker_id, config=config)
        code_project = await create_code_project(
            client,
            token,
            worker_id,
            config=config,
            source=source,
        )
        task = await create_task(
            client,
            token,
            code_project.project["id"],
            worker_id=worker_id,
            timeout_seconds=scenario.timeout_seconds,
            retry_count=scenario.retry_count,
            retry_delay=scenario.retry_delay,
        )
        yield ScenarioResources(source, code_project, task, scenario)
    except BaseException as exc:
        failure = exc
    cleanup_errors = await _cleanup_created(
        client,
        token,
        source=source,
        code_project=code_project,
        task=task,
    )
    if failure is not None and cleanup_errors:
        raise BaseExceptionGroup("E2E 场景和资源清理均失败", [failure, *cleanup_errors])
    if failure is not None:
        raise failure
    if cleanup_errors:
        raise ExceptionGroup("E2E 资源清理失败", cleanup_errors)


async def list_task_runs(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
) -> list[dict[str, Any]]:
    payload = await request_json(
        client,
        "GET",
        f"/tasks/{task_id}/runs",
        token=token,
        params={"page": 1, "size": RUN_PAGE_SIZE},
    )
    data = extract_data(payload)
    if isinstance(data, dict):
        return list(data.get("items", []))
    if isinstance(data, list):
        return data
    raise AssertionError(f"任务执行列表响应格式错误: {data!r}")


async def wait_for_status(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
    *,
    expected: frozenset[str],
    timeout: float,
    interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    observed: list[str] = []
    while time.monotonic() < deadline:
        runs = await list_task_runs(client, token, task_id)
        for run in runs:
            status = str(run.get("status"))
            observed.append(status)
            if status in expected:
                return run
        await asyncio.sleep(interval)
    raise AssertionError(f"等待状态 {sorted(expected)} 超时: observed={observed[-10:]}")


async def wait_for_run_count(
    client: httpx.AsyncClient,
    token: str,
    task_id: str,
    *,
    minimum: int,
    timeout: float,
    interval: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_runs: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last_runs = await list_task_runs(client, token, task_id)
        statuses = {str(run.get("status")) for run in last_runs[:minimum]}
        if len(last_runs) >= minimum and statuses <= TERMINAL_STATUSES:
            return last_runs
        await asyncio.sleep(interval)
    raise AssertionError(f"等待 {minimum} 次终态执行超时: runs={last_runs}")


async def trigger_and_wait(
    client: httpx.AsyncClient,
    token: str,
    resources: ScenarioResources,
    *,
    config: E2EConfig,
) -> dict[str, Any]:
    await trigger_task(client, token, resources.task["id"])
    return await wait_for_status(
        client,
        token,
        resources.task["id"],
        expected=frozenset({resources.scenario.expected_status}),
        timeout=config.poll_timeout,
        interval=config.poll_interval,
    )
