from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class MonitoringConfig:
    node_id: str
    redis_url: str
    report_interval: int
    timezone: str
    data_store_type: str

    @classmethod
    def load(cls) -> "MonitoringConfig":
        return cls(
            node_id=_env_str("SCRAPY_NODE_NAME", "Antcode-Worker"),
            redis_url=_env_str("REDIS_URL", "redis://127.0.0.1:6379/0"),
            report_interval=_env_int("MONITOR_REPORT_INTERVAL", 60),
            timezone=_env_str("SCRAPY_TIMEZONE", "Asia/Shanghai"),
            data_store_type=_env_str("DATA_STORE_TYPE", "kafka"),
        )

