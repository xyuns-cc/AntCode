"""Shared log transport byte limits."""

import os
from dataclasses import dataclass

KIBIBYTE = 1024
MEBIBYTE = KIBIBYTE * KIBIBYTE
DEFAULT_LOG_MAX_BATCH_BYTES = 8 * MEBIBYTE
DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES = 256 * KIBIBYTE

# json.dumps may encode one single-byte control character as six ASCII bytes
# (for example NUL -> ``\u0000``). The fixed reserve covers the bounded run ID,
# event ID, timestamp, source, keys, and remaining realtime envelope fields.
MAX_JSON_ESCAPED_BYTES_PER_CONTENT_BYTE = 6
SSE_LOG_ENVELOPE_RESERVE_BYTES = 16 * KIBIBYTE
MAX_SSE_LOG_MESSAGE_BYTES = (
    DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES * MAX_JSON_ESCAPED_BYTES_PER_CONTENT_BYTE + SSE_LOG_ENVELOPE_RESERVE_BYTES
)


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
        if self.max_entry_content_bytes > DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES:
            raise ValueError(
                "max_entry_content_bytes 不得超过共享单条日志上限: "
                f"{self.max_entry_content_bytes} > {DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES}"
            )

    @staticmethod
    def _require_positive(name: str, value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} 必须是正整数")
