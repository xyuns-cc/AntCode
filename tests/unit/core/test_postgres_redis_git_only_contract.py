import inspect
from pathlib import Path

import pytest
from antcode_core.application.services.crawl.backends import base as crawl_queue_backend
from antcode_core.application.services.crawl.backends import dedup_backend, progress_backend
from antcode_core.domain.schemas.project import (
    FileInfo,
    ProjectCodeCreateRequest,
    ProjectCodeUpdateRequest,
    ProjectCreateFormRequest,
    ProjectFileCreateRequest,
    ProjectFileUpdateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)
from antcode_core.domain.schemas.project_unified import UnifiedProjectUpdateRequest
from antcode_core.infrastructure.cache.cache import CacheConfig

REPO_ROOT = Path(__file__).parents[3]
MIGRATIONS_DIR = REPO_ROOT / "migrations" / "models"
MYSQL_ONLY_TOKENS = (
    "AUTO_INCREMENT",
    "CHARACTER SET",
    "ENGINE=",
    "DATABASE()",
)


def test_crawl_memory_queue_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("CRAWL_BACKEND", "memory")
    crawl_queue_backend.reset_queue_backend()
    with pytest.raises(ValueError, match="Redis"):
        crawl_queue_backend.get_queue_backend()


def test_crawl_state_backends_reject_process_memory(monkeypatch):
    monkeypatch.setenv("DEDUP_BACKEND", "memory")
    monkeypatch.setenv("PROGRESS_BACKEND", "memory")
    monkeypatch.delenv("CRAWL_BACKEND", raising=False)
    dedup_backend.reset_dedup_store()
    progress_backend.reset_progress_store()

    with pytest.raises(ValueError, match="Redis"):
        dedup_backend.get_dedup_store()
    with pytest.raises(ValueError, match="Redis"):
        progress_backend.get_progress_store()


def test_default_cache_config_has_no_memory_fallback():
    config = CacheConfig()

    assert not hasattr(config, "use_redis")
    assert "memory" not in inspect.getsource(CacheConfig).lower()


def test_project_public_contract_uses_repository_sources_only():
    schema_models = (
        ProjectFileCreateRequest,
        ProjectCodeCreateRequest,
        ProjectCreateFormRequest,
        ProjectCodeUpdateRequest,
        ProjectFileUpdateRequest,
        FileInfo,
        ProjectUpdateRequest,
        ProjectResponse,
        UnifiedProjectUpdateRequest,
    )
    banned_fields = {
        "source_type",
        "file_source_type",
        "code_source_type",
        "storage_type",
        "fallback_enabled",
        "git_url",
        "git_credential_id",
        "code_content",
    }

    for model in schema_models:
        assert not banned_fields.intersection(model.model_fields)


def test_project_models_have_no_storage_fallback_switch():
    from antcode_core.domain.models.project import Project

    assert "fallback_enabled" not in Project._meta.fields_map


def test_sql_migrations_are_postgresql_only_and_idempotent():
    """每个迁移都必须自带「重复执行安全」的证明，形式取决于它改的是结构还是数据。

    - DDL：``IF (NOT) EXISTS`` 或 pg_catalog 存在性判定。
    - 纯数据修复：``DO $$`` 块 + ``GET DIAGNOSTICS`` 计数 + ``RAISE EXCEPTION`` 守卫。
      三者合起来才构成证明——守卫保证脚本成功返回时库里已不存在待修形态，
      于是二次执行必然零命中；缺了计数就是无账可查的盲改，缺了守卫就会把修不动的
      残留静默留在库里，等到运行期才炸。
    """
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migrations

    for path in migrations:
        content = path.read_text(encoding="utf-8")
        assert not any(token in content for token in MYSQL_ONLY_TOKENS)
        has_exists_guard = "IF NOT EXISTS" in content or "IF EXISTS" in content
        has_catalog_guard = "DO $$" in content and "pg_constraint" in content
        has_data_repair_guard = all(token in content for token in ("DO $$", "GET DIAGNOSTICS", "RAISE EXCEPTION"))
        assert has_exists_guard or has_catalog_guard or has_data_repair_guard, path


def test_current_master_control_modules_replace_legacy_loops_package():
    master = REPO_ROOT / "services/master/src/antcode_master"

    assert (master / "control/scheduler_loop.py").exists()
    assert (master / "control/dispatcher_loop.py").exists()
    assert (master / "ingester/result_loop.py").exists()
    assert not (master / "loops").exists()


def test_scheduler_publishes_through_persistent_outbox():
    source = (
        REPO_ROOT / "packages/antcode_core/src/antcode_core/application/services/scheduler/scheduler_service.py"
    ).read_text(encoding="utf-8")

    assert "scheduler_outbox_service.enqueue" in source
    assert "asyncio.Queue" not in source


def test_retry_and_redispatch_services_use_persistent_backends():
    service_dir = REPO_ROOT / "packages/antcode_core/src/antcode_core/application/services/scheduler"

    retry_source = (service_dir / "retry_service.py").read_text(encoding="utf-8")
    redispatch_source = (service_dir / "redispatch_service.py").read_text(encoding="utf-8")

    assert "TaskRun" in retry_source
    assert "zadd" in redispatch_source
    assert "processing" in redispatch_source
    assert "deque(" not in retry_source + redispatch_source
    assert "asyncio.Queue" not in retry_source + redispatch_source


def test_worker_credentials_have_no_file_store_default():
    source = (REPO_ROOT / "services/worker/src/antcode_worker/security/identity.py").read_text(encoding="utf-8")

    assert "credentials.json" not in source
    assert "FileCredentialStore" not in source


def test_frontend_auth_has_no_plaintext_password_fallback():
    frontend = REPO_ROOT / "web/antcode-frontend/src"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (frontend / "pages/Login/index.tsx", frontend / "services/auth.ts")
    )

    assert "localStorage.setItem('password'" not in source
    assert 'localStorage.setItem("password"' not in source
    assert "rememberedPassword" not in source


def test_log_stream_auth_has_no_access_token_url_fallback():
    source = (REPO_ROOT / "web/antcode-frontend/src/services/logs.ts").read_text(encoding="utf-8")

    assert "STORAGE_KEYS.ACCESS_TOKEN" not in source
    assert "localStorage" not in source
    assert "回退到 access_token" not in source


def test_task_logs_upgrade_migration_exists():
    migration = MIGRATIONS_DIR / "20260710_add_task_logs.sql"
    source = migration.read_text(encoding="utf-8")

    assert 'CREATE TABLE IF NOT EXISTS "task_logs"' in source
    assert "idx_task_logs_event_id_unique" in source
