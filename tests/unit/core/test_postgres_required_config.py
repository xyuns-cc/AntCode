import ssl

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


def test_tortoise_config_pins_public_search_path(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://antcode:secret@127.0.0.1:5432/antcode",
    )

    credentials = tortoise.get_tortoise_config()["connections"]["default"]["credentials"]

    assert credentials["server_settings"] == {"search_path": "public"}


def test_tortoise_config_preserves_required_tls_mode(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://antcode:secret@127.0.0.1:5432/antcode?sslmode=require",
    )

    credentials = tortoise.get_tortoise_config()["connections"]["default"]["credentials"]

    assert credentials["ssl"] == "require"


def test_tortoise_config_decodes_percent_encoded_credentials_and_database(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://service%40tenant:p%40ss%3Aword@127.0.0.1:5432/antcode%20prod",
    )

    credentials = tortoise.get_tortoise_config()["connections"]["default"]["credentials"]

    assert credentials["user"] == "service@tenant"
    assert credentials["password"] == "p@ss:word"
    assert credentials["database"] == "antcode prod"


def test_tortoise_config_loads_root_cert_for_verify_full(monkeypatch, tmp_path):
    root_cert = tmp_path / "postgres-ca.pem"
    root_cert.write_text("test certificate", encoding="utf-8")
    loaded: list[str] = []
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def fake_create_default_context(*, cafile):
        loaded.append(cafile)
        return context

    monkeypatch.setattr(tortoise.ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://antcode:secret@127.0.0.1:5432/antcode?sslmode=verify-full&sslrootcert={root_cert}",
    )

    credentials = tortoise.get_tortoise_config()["connections"]["default"]["credentials"]

    assert credentials["ssl"] is context
    assert context.check_hostname is True
    assert loaded == [str(root_cert)]


@pytest.mark.parametrize(
    "query, expected",
    [
        ("sslmode=unknown", "sslmode 无效"),
        ("sslmode=verify-ca", "必须显式设置 sslrootcert"),
        ("sslrootcert=/tmp/ca.pem", "必须与 sslmode 一起设置"),
        ("sslmode=disable&sslrootcert=/tmp/ca.pem", "禁止设置 sslrootcert"),
        ("sslmode=prefer&sslrootcert=/tmp/ca.pem", "不会校验证书"),
        ("sslmode=require&sslmode=verify-full", "只能设置一次"),
        ("sslmode=require&sslcert=/tmp/client.pem", "不支持的 TLS 参数: sslcert"),
    ],
)
def test_tortoise_config_rejects_invalid_tls_options(monkeypatch, query, expected):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"postgresql://antcode:secret@127.0.0.1:5432/antcode?{query}",
    )

    with pytest.raises(ValueError, match=expected):
        tortoise.get_tortoise_config()


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


def test_backendless_gateway_worker_rejects_backend_credentials():
    settings = Settings(
        DATABASE_URL="",
        REDIS_URL="",
        WORKER_TRANSPORT_MODE="gateway",
        WORKER_GATEWAY_BACKENDLESS=True,
    )

    assert settings.WORKER_GATEWAY_BACKENDLESS is True

    with pytest.raises(ValueError, match="禁止注入"):
        Settings(
            DATABASE_URL="postgresql://antcode:secret@127.0.0.1:5432/antcode",
            REDIS_URL="",
            WORKER_TRANSPORT_MODE="gateway",
            WORKER_GATEWAY_BACKENDLESS=True,
        )


def test_backendless_mode_rejects_direct_worker():
    with pytest.raises(ValueError, match="仅允许 Gateway Worker"):
        Settings(
            DATABASE_URL="",
            REDIS_URL="",
            WORKER_TRANSPORT_MODE="direct",
            WORKER_GATEWAY_BACKENDLESS=True,
        )


def test_direct_worker_keeps_postgres_and_drops_control_plane_redis():
    """Direct Worker 的后端边界是非对称的：要 PG（产物平面），不要控制面 Redis。"""
    settings = Settings(
        DATABASE_URL="postgresql://antcode:secret@127.0.0.1:5432/antcode",
        REDIS_URL="",
        WORKER_TRANSPORT_MODE="direct",
        WORKER_DIRECT_SCOPED_REDIS=True,
    )

    assert settings.WORKER_DIRECT_SCOPED_REDIS is True


def test_direct_worker_rejects_control_plane_redis_url():
    with pytest.raises(ValueError, match="只能使用 WORKER_REDIS_URL"):
        Settings(
            DATABASE_URL="postgresql://antcode:secret@127.0.0.1:5432/antcode",
            REDIS_URL="redis://127.0.0.1:6379/0",
            WORKER_TRANSPORT_MODE="direct",
            WORKER_DIRECT_SCOPED_REDIS=True,
        )


def test_direct_worker_still_requires_database_url():
    """产物平面直连 PG，缺 DATABASE_URL 必须启动即失败，而不是取源码时才炸。"""
    with pytest.raises(ValueError, match="DATABASE_URL 必须设置"):
        Settings(
            DATABASE_URL="",
            REDIS_URL="",
            WORKER_TRANSPORT_MODE="direct",
            WORKER_DIRECT_SCOPED_REDIS=True,
        )


def test_scoped_redis_mode_rejects_gateway_worker():
    with pytest.raises(ValueError, match="仅允许 Direct Worker"):
        Settings(
            DATABASE_URL="postgresql://antcode:secret@127.0.0.1:5432/antcode",
            REDIS_URL="",
            WORKER_TRANSPORT_MODE="gateway",
            WORKER_DIRECT_SCOPED_REDIS=True,
        )


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
