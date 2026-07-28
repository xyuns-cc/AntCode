"""Redis connection URLs must be safe to include in logs and status output."""

from antcode_core.infrastructure.redis.url_security import redact_redis_url


def test_redact_redis_url_masks_standard_password() -> None:
    url = "redis://default:secret-value@redis.internal:6379/14"

    redacted = redact_redis_url(url)

    assert redacted == "redis://default:***@redis.internal:6379/14"
    assert "secret-value" not in redacted


def test_redact_redis_url_masks_cluster_password() -> None:
    url = "rediss+cluster://worker:secret@redis.internal:6379/0"

    assert redact_redis_url(url) == "rediss+cluster://worker:***@redis.internal:6379/0"


def test_redact_redis_url_masks_sentinel_password() -> None:
    url = "redis+sentinel://secret@primary@sentinel-a:26379,sentinel-b:26379/2"

    redacted = redact_redis_url(url)

    assert redacted == "redis+sentinel://***@primary@sentinel-a:26379,sentinel-b:26379/2"
    assert "secret" not in redacted


def test_redact_redis_url_masks_all_sentinel_password_sources() -> None:
    url = (
        "rediss+sentinel://legacy-user:legacy-secret@primary@sentinel-a:26379/2"
        "?sentinel_username=sentinel-user&sentinel_password=sentinel-secret"
        "&master_username=master-user&master_password=master-secret"
    )

    redacted = redact_redis_url(url)

    assert "legacy-secret" not in redacted
    assert "sentinel-secret" not in redacted
    assert "master-secret" not in redacted
    assert "legacy-user:***@primary" in redacted
    assert "sentinel_password=***" in redacted
    assert "master_password=***" in redacted


def test_redact_redis_url_preserves_urls_without_passwords() -> None:
    urls = [
        "redis://redis.internal:6379/0",
        "redis+sentinel://primary@sentinel-a:26379/0",
        "not-a-url",
    ]

    assert [redact_redis_url(url) for url in urls] == urls
