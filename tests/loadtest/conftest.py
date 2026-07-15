from __future__ import annotations

import os
from collections.abc import Callable

import pytest
import pytest_asyncio

from tests.loadtest.tool.binding import verify_redis_target_binding
from tests.loadtest.tool.config import (
    DEFAULT_BACKLOG_TIMEOUT_SECONDS,
    DEFAULT_MAX_ERROR_RATE,
    DEFAULT_MAX_P50_MS,
    DEFAULT_MAX_P95_MS,
    DEFAULT_MAX_P99_MS,
    DEFAULT_MIN_LOG_LINES,
    DEFAULT_MIN_QPS_RATIO,
    DEFAULT_MIN_WORKERS,
    DEFAULT_TIMEOUT_SECONDS,
    FULL_CONFIRMATION,
    READ_ONLY_CONFIRMATION,
    LoadSettings,
    Thresholds,
    load_tokens,
    nonnegative_ratio,
    parse_csv,
    parse_stage,
    positive_float,
    positive_int,
    validate_base_url,
    validate_confirmation,
    validate_redis_binding_key,
    validate_redis_url,
)

ENV_PREFIX = "ANTCODE_LOADTEST_"
LOAD_SETTINGS_KEY: pytest.StashKey[LoadSettings] = pytest.StashKey()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("AntCode load tests")
    group.addoption("--run-loadtests", action="store_true", help="Enable guarded load-test scenarios")
    _add(group, "base-url", "Target AntCode base URL")
    _add(group, "redis-url", "Redis URL with a non-zero database and target binding marker")
    _add(group, "redis-binding-key", "Redis key containing the exact target binding marker")
    _add(group, "token-file", "Owner-only file containing one bearer token per line")
    _add(group, "stage", "Load stage formatted as VUS:QPS:DURATION_SECONDS")
    _add(group, "project-id", "Dedicated load-test project public ID")
    _add(group, "worker-id", "Dedicated load-test Worker public ID")
    _add(group, "run-ids", "Comma-separated run IDs with retained logs")
    _add(group, "churn-worker-ids", "Comma-separated externally restarted Worker IDs")
    _add(group, "min-workers", "Minimum accessible Worker count")
    _add(group, "min-log-lines", "Minimum historical lines required per WebSocket run")
    _add(group, "timeout-seconds", "Per-operation timeout")
    _add(group, "backlog-timeout-seconds", "Backlog completion deadline")
    _add(group, "max-p50-ms", "Maximum P50 latency")
    _add(group, "max-p95-ms", "Maximum P95 latency")
    _add(group, "max-p99-ms", "Maximum P99 latency")
    _add(group, "max-error-rate", "Maximum request error rate")
    _add(group, "min-qps-ratio", "Minimum achieved/target QPS ratio")


def _add(group: pytest.OptionGroup, name: str, help_text: str) -> None:
    group.addoption(f"--loadtest-{name}", action="store", default=None, help=help_text)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "loadtest_scenario: guarded external load-test scenario")
    config.addinivalue_line("markers", "loadtest_write: scenario that creates or triggers tasks")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    scenarios = [item for item in items if item.get_closest_marker("loadtest_scenario")]
    if not config.getoption("--run-loadtests"):
        _deselect(config, items, scenarios)
        return
    try:
        confirmation = validate_confirmation(os.getenv(f"{ENV_PREFIX}CONFIRM"))
        settings = _build_settings(lambda name: _value(config, name))
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    except OSError as exc:
        raise pytest.UsageError(str(exc)) from exc
    config.stash[LOAD_SETTINGS_KEY] = settings
    if confirmation == READ_ONLY_CONFIRMATION:
        write_items = [item for item in scenarios if item.get_closest_marker("loadtest_write")]
        _deselect(config, items, write_items)


def _deselect(config: pytest.Config, items: list[pytest.Item], selected: list[pytest.Item]) -> None:
    if not selected:
        return
    selected_ids = {id(item) for item in selected}
    items[:] = [item for item in items if id(item) not in selected_ids]
    config.hook.pytest_deselected(items=selected)


def _value(config: pytest.Config, name: str) -> str | None:
    cli_value = config.getoption(f"--loadtest-{name}")
    env_name = f"{ENV_PREFIX}{name.replace('-', '_').upper()}"
    return cli_value or os.getenv(env_name)


@pytest_asyncio.fixture(scope="session")
async def load_settings(pytestconfig: pytest.Config) -> LoadSettings:
    settings = pytestconfig.stash[LOAD_SETTINGS_KEY]
    await verify_redis_target_binding(settings)
    return settings


def _build_settings(value: Callable[[str], str | None]) -> LoadSettings:
    confirmation = validate_confirmation(os.getenv(f"{ENV_PREFIX}CONFIRM"))
    if confirmation not in {READ_ONLY_CONFIRMATION, FULL_CONFIRMATION}:
        raise ValueError("invalid load-test confirmation")
    thresholds = Thresholds(
        max_p50_ms=positive_float(value("max-p50-ms"), "max P50", DEFAULT_MAX_P50_MS),
        max_p95_ms=positive_float(value("max-p95-ms"), "max P95", DEFAULT_MAX_P95_MS),
        max_p99_ms=positive_float(value("max-p99-ms"), "max P99", DEFAULT_MAX_P99_MS),
        max_error_rate=nonnegative_ratio(value("max-error-rate"), "max error rate", DEFAULT_MAX_ERROR_RATE),
        min_qps_ratio=nonnegative_ratio(value("min-qps-ratio"), "min QPS ratio", DEFAULT_MIN_QPS_RATIO),
    )
    return LoadSettings(
        base_url=validate_base_url(value("base-url")),
        redis_url=validate_redis_url(value("redis-url")),
        redis_binding_key=validate_redis_binding_key(value("redis-binding-key")),
        tokens=load_tokens(value("token-file")),
        confirmation=confirmation,
        stage=parse_stage(value("stage")),
        thresholds=thresholds,
        timeout_seconds=positive_float(value("timeout-seconds"), "timeout", DEFAULT_TIMEOUT_SECONDS),
        backlog_timeout_seconds=positive_float(
            value("backlog-timeout-seconds"), "backlog timeout", DEFAULT_BACKLOG_TIMEOUT_SECONDS
        ),
        project_id=value("project-id"),
        worker_id=value("worker-id"),
        run_ids=parse_csv(value("run-ids")),
        churn_worker_ids=parse_csv(value("churn-worker-ids")),
        min_workers=positive_int(value("min-workers"), "minimum workers", DEFAULT_MIN_WORKERS),
        min_log_lines=positive_int(value("min-log-lines"), "minimum log lines", DEFAULT_MIN_LOG_LINES),
    )
