from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

READ_ONLY_CONFIRMATION = "READ_ONLY"
FULL_CONFIRMATION = "FULL"
VALID_CONFIRMATIONS = frozenset({READ_ONLY_CONFIRMATION, FULL_CONFIRMATION})
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_BACKLOG_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_P50_MS = 500.0
DEFAULT_MAX_P95_MS = 1_000.0
DEFAULT_MAX_P99_MS = 2_000.0
DEFAULT_MAX_ERROR_RATE = 0.01
DEFAULT_MIN_QPS_RATIO = 0.8
DEFAULT_MIN_WORKERS = 1
DEFAULT_MIN_LOG_LINES = 1
MAX_REDIS_BINDING_KEY_LENGTH = 256


@dataclass(frozen=True)
class Stage:
    vus: int
    qps: float
    duration_seconds: float

    @property
    def request_count(self) -> int:
        return max(1, round(self.qps * self.duration_seconds))


@dataclass(frozen=True)
class Thresholds:
    max_p50_ms: float = DEFAULT_MAX_P50_MS
    max_p95_ms: float = DEFAULT_MAX_P95_MS
    max_p99_ms: float = DEFAULT_MAX_P99_MS
    max_error_rate: float = DEFAULT_MAX_ERROR_RATE
    min_qps_ratio: float = DEFAULT_MIN_QPS_RATIO


@dataclass(frozen=True)
class LoadSettings:
    base_url: str
    redis_url: str = field(repr=False)
    redis_binding_key: str
    tokens: tuple[str, ...] = field(repr=False)
    confirmation: str
    stage: Stage
    thresholds: Thresholds
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    backlog_timeout_seconds: float = DEFAULT_BACKLOG_TIMEOUT_SECONDS
    project_id: str | None = None
    worker_id: str | None = None
    run_ids: tuple[str, ...] = ()
    churn_worker_ids: tuple[str, ...] = ()
    min_workers: int = DEFAULT_MIN_WORKERS
    min_log_lines: int = DEFAULT_MIN_LOG_LINES


def validate_confirmation(value: str | None) -> str:
    if value not in VALID_CONFIRMATIONS:
        allowed = ", ".join(sorted(VALID_CONFIRMATIONS))
        raise ValueError(f"ANTCODE_LOADTEST_CONFIRM must be one of: {allowed}")
    return value


def validate_base_url(value: str | None) -> str:
    if not value:
        raise ValueError("load-test base URL is required")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an explicit http(s) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain query or fragment components")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def validate_redis_url(value: str | None) -> str:
    if not value:
        raise ValueError("load-test Redis URL is required as a safety guard")
    parsed = urlsplit(value)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise ValueError("Redis URL must use redis:// or rediss://")
    database = parsed.path.strip("/")
    if not database.isdigit():
        raise ValueError("Redis URL must include an explicit numeric database")
    if int(database) == 0:
        raise ValueError("Redis DB0 is forbidden for load tests")
    return value


def validate_redis_binding_key(value: str | None) -> str:
    if not value:
        raise ValueError("load-test Redis binding key is required")
    if len(value) > MAX_REDIS_BINDING_KEY_LENGTH or any(character.isspace() for character in value):
        raise ValueError("Redis binding key must be whitespace-free and at most 256 characters")
    return value


def masked_redis_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    username = f"{parsed.username}:***@" if parsed.username else "***@"
    return urlunsplit((parsed.scheme, f"{username}{host}{port}", parsed.path, "", ""))


def parse_stage(value: str | None) -> Stage:
    if not value:
        raise ValueError("load-test stage is required (VUS:QPS:DURATION_SECONDS)")
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("stage must use VUS:QPS:DURATION_SECONDS")
    try:
        stage = Stage(int(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError as exc:
        raise ValueError("stage values must be numeric") from exc
    if stage.vus <= 0 or stage.qps <= 0 or stage.duration_seconds <= 0:
        raise ValueError("stage values must be greater than zero")
    return stage


def load_tokens(path_value: str | None) -> tuple[str, ...]:
    if not path_value:
        raise ValueError("load-test token file is required")
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise ValueError("token file must be a regular file")
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name == "posix" and mode & 0o077:
        raise ValueError("token file permissions must be owner-only (chmod 600)")
    tokens = tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if not tokens:
        raise ValueError("token file is empty")
    return tokens


def parse_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def positive_float(value: str | float | None, name: str, default: float) -> float:
    parsed = default if value is None or value == "" else float(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def nonnegative_ratio(value: str | float | None, name: str, default: float) -> float:
    parsed = default if value is None or value == "" else float(value)
    if not 0 <= parsed <= 1:
        raise ValueError(f"{name} must be between zero and one")
    return parsed


def positive_int(value: str | int | None, name: str, default: int) -> int:
    parsed = default if value is None or value == "" else int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed
