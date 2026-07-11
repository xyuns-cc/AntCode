from antcode_core.application.services.logs.log_cleanup_service import CleanupResult, LogCleanupService
from antcode_core.common.config import settings


def test_cleanup_result_defaults():
    result = CleanupResult()
    assert result.postgres_rows_deleted == 0
    assert result.redis_streams_checked == 0
    assert result.redis_streams_trimmed == 0
    assert result.redis_streams_expired == 0
    assert result.errors == []


def test_redis_patterns_cover_realtime_and_chunk_streams():
    service = LogCleanupService()
    patterns = service._redis_patterns()
    assert len(patterns) == 2
    assert all(pattern for pattern, _maxlen, _ttl in patterns)


def test_log_stream_settings_present():
    assert settings.LOG_STREAM_MAXLEN > 0
    assert settings.LOG_STREAM_TTL_SECONDS > 0
