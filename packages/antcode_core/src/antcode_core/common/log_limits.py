"""Shared log transport byte limits."""

import os
from dataclasses import dataclass

MEBIBYTE = 1024 * 1024
DEFAULT_LOG_MAX_BATCH_BYTES = 8 * MEBIBYTE
DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES = MEBIBYTE


def positive_env_int(name: str, default: int) -> int:
    """Read a positive integer environment setting without fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


@dataclass(frozen=True)
class LogBatchLimits:
    """Byte budgets applied by both Worker and Gateway."""

    max_batch_bytes: int = DEFAULT_LOG_MAX_BATCH_BYTES
    max_entry_content_bytes: int = DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES

    def __post_init__(self) -> None:
        self._require_positive("max_batch_bytes", self.max_batch_bytes)
        self._require_positive("max_entry_content_bytes", self.max_entry_content_bytes)

    @staticmethod
    def _require_positive(name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必须是正整数")
