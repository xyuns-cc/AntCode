"""验证 DB 连接池按 service 名独立解析。"""

from antcode_core.common.config import settings


def test_db_pool_max_for_known_services():
    assert settings.db_pool_max_for("web_api") == settings.DB_POOL_MAX_WEB_API
    assert settings.db_pool_max_for("master") == settings.DB_POOL_MAX_MASTER
    assert settings.db_pool_max_for("gateway") == settings.DB_POOL_MAX_GATEWAY
    assert settings.db_pool_max_for("worker") == settings.DB_POOL_MAX_WORKER


def test_db_pool_max_for_unknown_service_falls_back_to_default():
    assert settings.db_pool_max_for("some-new-service") == settings.DB_POOL_MAX
    assert settings.db_pool_max_for(None) == settings.DB_POOL_MAX
    assert settings.db_pool_max_for("") == settings.DB_POOL_MAX


def test_db_pool_max_case_and_dash_insensitive():
    assert settings.db_pool_max_for("WEB_API") == settings.DB_POOL_MAX_WEB_API
    assert settings.db_pool_max_for("web-api") == settings.DB_POOL_MAX_WEB_API


def test_get_tortoise_config_resolves_pool_per_service(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/antcode_test")
    from antcode_core.infrastructure.db import tortoise as tortoise_mod

    cfg = tortoise_mod.get_tortoise_config(service="worker")
    pool = cfg["connections"]["default"]
    assert pool["maxsize"] == settings.DB_POOL_MAX_WORKER
    assert pool["minsize"] == settings.DB_POOL_MIN

    cfg_web = tortoise_mod.get_tortoise_config(service="web_api")
    assert cfg_web["connections"]["default"]["maxsize"] == settings.DB_POOL_MAX_WEB_API


def test_get_tortoise_config_explicit_overrides_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/antcode_test")
    from antcode_core.infrastructure.db import tortoise as tortoise_mod

    cfg = tortoise_mod.get_tortoise_config(service="worker", min_connections=1, max_connections=3)
    pool = cfg["connections"]["default"]
    assert pool["minsize"] == 1
    assert pool["maxsize"] == 3


def test_min_clamped_to_max(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/antcode_test")
    from antcode_core.infrastructure.db import tortoise as tortoise_mod

    cfg = tortoise_mod.get_tortoise_config(service="worker", min_connections=100, max_connections=5)
    pool = cfg["connections"]["default"]
    assert pool["minsize"] == 5
    assert pool["maxsize"] == 5
