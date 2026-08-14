import pytest
from antcode_core.common.config import Settings
from antcode_core.common.log_limits import (
    DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES,
    MAX_SSE_LOG_MESSAGE_BYTES,
    LogBatchLimits,
)

_BACKEND_CONFIG = {
    "DATABASE_URL": "postgresql://antcode:secret@127.0.0.1:5432/antcode",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
}


def test_sse_queue_must_exceed_worst_case_log_message_budget():
    with pytest.raises(ValueError, match="SSE_QUEUE_MAX_BYTES"):
        Settings(**_BACKEND_CONFIG, SSE_QUEUE_MAX_BYTES=MAX_SSE_LOG_MESSAGE_BYTES)


def test_sse_queue_accepts_one_byte_above_worst_case_budget():
    settings = Settings(
        **_BACKEND_CONFIG,
        SSE_QUEUE_MAX_BYTES=MAX_SSE_LOG_MESSAGE_BYTES + 1,
    )

    assert settings.SSE_QUEUE_MAX_BYTES == MAX_SSE_LOG_MESSAGE_BYTES + 1


def test_log_batch_limits_reject_entry_override_above_shared_limit():
    with pytest.raises(ValueError, match="不得超过共享单条日志上限"):
        LogBatchLimits(max_entry_content_bytes=DEFAULT_LOG_MAX_ENTRY_CONTENT_BYTES + 1)
