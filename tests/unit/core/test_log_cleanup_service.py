from antcode_core.common.config import settings
from antcode_core.application.services.logs.log_cleanup_service import CleanupResult, LogCleanupService


def test_cleanup_result_defaults():
    result = CleanupResult()
    assert result.execution_ids == []
    assert result.errors == []


def test_format_bytes():
    assert LogCleanupService._format_bytes(1024) == "1.0KB"


def test_log_stream_settings_present():
    assert settings.LOG_STREAM_MAXLEN > 0
    assert settings.LOG_STREAM_TTL_SECONDS > 0
