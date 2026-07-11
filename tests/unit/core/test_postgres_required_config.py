import pytest
from antcode_core.common.config import Settings
from antcode_core.infrastructure.db import tortoise


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL 必须设置"):
        tortoise.get_database_url()


def test_database_url_must_be_postgresql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp.db")

    with pytest.raises(ValueError, match="只能使用 PostgreSQL"):
        tortoise.get_database_url()


def test_tortoise_config_uses_asyncpg(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://antcode:secret@127.0.0.1:5432/antcode",
    )

    config = tortoise.get_tortoise_config()

    assert config["connections"]["default"]["engine"] == "tortoise.backends.asyncpg"


@pytest.mark.parametrize(
    "database_url, expected",
    [
        ("postgresql://antcode:secret@:5432/antcode", "host"),
        ("postgresql://:secret@127.0.0.1:5432/antcode", "user"),
        ("postgresql://antcode@127.0.0.1:5432/antcode", "password"),
        ("postgresql://antcode:secret@127.0.0.1:5432", "database"),
    ],
)
def test_tortoise_config_rejects_incomplete_postgres_url(
    monkeypatch,
    database_url,
    expected,
):
    monkeypatch.setenv("DATABASE_URL", database_url)

    with pytest.raises(ValueError, match=expected):
        tortoise.get_tortoise_config()


def test_settings_require_postgres_and_redis():
    settings = Settings(
        DATABASE_URL="postgresql://antcode:secret@127.0.0.1:5432/antcode",
        REDIS_URL="redis://127.0.0.1:6379/0",
    )

    assert settings.db_url.startswith("postgresql://")
    assert not hasattr(settings, "REDIS_ENABLED")
    assert not hasattr(settings, "LOG_STORAGE_BACKEND")
    assert not hasattr(settings, "FILE_STORAGE_BACKEND")


@pytest.mark.parametrize(
    "database_url, expected",
    [
        ("postgresql://antcode:secret@:5432/antcode", "host"),
        ("postgresql://:secret@127.0.0.1:5432/antcode", "user"),
        ("postgresql://antcode@127.0.0.1:5432/antcode", "password"),
        ("postgresql://antcode:secret@127.0.0.1:5432", "database"),
    ],
)
def test_settings_reject_incomplete_postgres_url(database_url, expected):
    with pytest.raises(ValueError, match=expected):
        Settings(
            DATABASE_URL=database_url,
            REDIS_URL="redis://127.0.0.1:6379/0",
        )


@pytest.mark.parametrize(
    "redis_url, expected",
    [
        ("", "REDIS_URL"),
        ("http://127.0.0.1:6379/0", "REDIS_URL"),
        ("redis://:secret@:6379/0", "host"),
    ],
)
def test_settings_reject_invalid_redis_url(redis_url, expected):
    with pytest.raises(ValueError, match=expected):
        Settings(
            DATABASE_URL="postgresql://antcode:secret@127.0.0.1:5432/antcode",
            REDIS_URL=redis_url,
        )
