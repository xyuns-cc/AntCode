import inspect
from pathlib import Path

import pytest
from antcode_core.application.services.crawl.backends import base as crawl_queue_backend
from antcode_core.application.services.crawl.backends import dedup_backend, progress_backend
from antcode_core.application.services.scheduler import queue_backend
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


@pytest.mark.parametrize("env_name", ["QUEUE_BACKEND", "CRAWL_BACKEND"])
def test_memory_queue_backends_are_rejected(monkeypatch, env_name):
    monkeypatch.setenv(env_name, "memory")
    if env_name == "QUEUE_BACKEND":
        queue_backend.reset_queue_backend()
        with pytest.raises(ValueError, match="Redis"):
            queue_backend.get_queue_backend()
        return

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
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    assert migrations

    for path in migrations:
        content = path.read_text(encoding="utf-8")
        assert not any(token in content for token in MYSQL_ONLY_TOKENS)
        assert "IF NOT EXISTS" in content or "IF EXISTS" in content


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


def test_log_websocket_auth_has_no_access_token_url_fallback():
    source = (REPO_ROOT / "web/antcode-frontend/src/services/logs.ts").read_text(encoding="utf-8")

    assert "STORAGE_KEYS.ACCESS_TOKEN" not in source
    assert "localStorage" not in source
    assert "回退到 access_token" not in source


def test_task_logs_upgrade_migration_exists():
    migration = MIGRATIONS_DIR / "20260710_add_task_logs.sql"
    source = migration.read_text(encoding="utf-8")

    assert 'CREATE TABLE IF NOT EXISTS "task_logs"' in source
    assert "idx_task_logs_event_id_unique" in source
