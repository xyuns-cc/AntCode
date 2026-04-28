# Control Plane Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make production single-node and distributed AntCode deployments share one DB/Redis/lock/scheduler correctness model.

**Architecture:** Start with production profile validation and fail-fast startup, then add explicit DB migration ownership, Redis lease/fencing primitives, DB-first scheduler claiming, and result/ACK correctness. DB remains the durable source of truth; Redis remains control-plane transport and short-lived coordination.

**Tech Stack:** Python 3.12, Pydantic Settings, Tortoise ORM/Aerich, Redis Streams, pytest/pytest-asyncio.

---

### Task 1: Production Profile Validation

**Files:**
- Modify: `packages/antcode_core/src/antcode_core/common/config.py`
- Test: `tests/unit/core/test_deployment_profile_config.py`

- [ ] **Step 1: Write failing tests**

Create tests that instantiate `Settings` directly with `_env_file=None` and assert:

```python
import pytest
from pydantic import ValidationError

from antcode_core.common.config import Settings


def build_settings(**overrides):
    defaults = {
        "ANTCODE_PROFILE": "single-node",
        "DATABASE_URL": "mysql+asyncmy://user:pass@db:3306/antcode",
        "REDIS_URL": "redis://redis:6379/0",
        "FILE_STORAGE_BACKEND": "s3",
        "LOG_STORAGE_BACKEND": "s3",
        "JWT_SECRET_KEY": "x" * 32,
        "ENCRYPTION_KEY": "y" * 32,
        "DEFAULT_ADMIN_PASSWORD": "change-me",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def test_single_node_requires_database_url():
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        build_settings(DATABASE_URL="")


def test_single_node_rejects_sqlite():
    with pytest.raises(ValidationError, match="SQLite"):
        build_settings(DATABASE_URL="sqlite:///tmp/dev.db")


def test_single_node_requires_redis():
    with pytest.raises(ValidationError, match="REDIS_URL"):
        build_settings(REDIS_URL="")


def test_single_node_requires_s3_file_storage():
    with pytest.raises(ValidationError, match="FILE_STORAGE_BACKEND"):
        build_settings(FILE_STORAGE_BACKEND="local")


def test_dev_allows_sqlite_and_local_storage():
    settings = Settings(
        _env_file=None,
        ANTCODE_PROFILE="dev",
        DATABASE_URL="",
        REDIS_URL="",
        FILE_STORAGE_BACKEND="local",
        LOG_STORAGE_BACKEND="local",
    )
    assert settings.ANTCODE_PROFILE == "dev"
    assert settings.REDIS_ENABLED is False
```

- [ ] **Step 2: Run RED**

Run: `timeout 60 uv run pytest tests/unit/core/test_deployment_profile_config.py -q`

Expected: failures because `ANTCODE_PROFILE`, production validation, and required secret fields are not implemented.

- [ ] **Step 3: Implement settings validation**

Add `ANTCODE_PROFILE`, `JWT_SECRET_KEY`, production profile helpers, and fail-fast validation inside `Settings.validate_backend_config`. Keep `dev` permissive. Make `single-node` and `distributed` reject missing DB, SQLite DB, missing Redis, non-S3 file storage, unsupported log storage, and missing production secrets.

- [ ] **Step 4: Run GREEN**

Run: `timeout 60 uv run pytest tests/unit/core/test_deployment_profile_config.py -q`

Expected: all tests pass.

### Task 2: Production Startup Fail-Fast

**Files:**
- Modify: `services/web_api/src/antcode_web_api/lifespan.py`
- Modify: `packages/antcode_core/src/antcode_core/infrastructure/db/tortoise.py`
- Test: `tests/unit/web_api/test_lifespan_production_startup.py`

- [ ] **Step 1: Write failing tests**

Test that production startup calls DB schema validation without `Tortoise.generate_schemas`, and Redis init raises `SystemExit` on connection failure in production.

- [ ] **Step 2: Run RED**

Run: `timeout 60 uv run pytest tests/unit/web_api/test_lifespan_production_startup.py -q`

Expected: failures because current code auto-generates schemas and only warns on Redis failure.

- [ ] **Step 3: Implement startup split**

Add profile-aware startup behavior:

- dev: current schema generation remains allowed.
- single-node/distributed: no schema generation; call explicit migration/schema check.
- Redis failure in production raises `SystemExit`.

- [ ] **Step 4: Run GREEN**

Run: `timeout 60 uv run pytest tests/unit/web_api/test_lifespan_production_startup.py -q`

Expected: all tests pass.

### Task 3: Redis Lease And Fencing Contract

**Files:**
- Modify: `packages/antcode_core/src/antcode_core/infrastructure/redis/locks.py`
- Test: `tests/unit/core/test_redis_leases.py`

- [ ] **Step 1: Write failing tests**

Test lock values contain `owner_id`, `token`, and `expires_at`; renewal only succeeds for the same owner/token; renewal failure clears local leadership eligibility.

- [ ] **Step 2: Run RED**

Run: `timeout 60 uv run pytest tests/unit/core/test_redis_leases.py -q`

Expected: failures because current lock stores only a random token string and health check only checks local state.

- [ ] **Step 3: Implement lease value and Lua scripts**

Change acquire/release/extend to use JSON lease values and Lua compare/update scripts. Keep public APIs compatible where possible.

- [ ] **Step 4: Run GREEN**

Run: `timeout 60 uv run pytest tests/unit/core/test_redis_leases.py -q`

Expected: all tests pass.

### Task 4: DB-First Scheduler Claiming

**Files:**
- Modify: `packages/antcode_core/src/antcode_core/domain/models/task_run.py`
- Create: migration under `migrations/models/`
- Create: `services/master/src/antcode_master/scheduling/fire_claims.py`
- Modify: `services/master/src/antcode_master/loops/scheduler_loop.py`
- Test: `tests/unit/master/test_scheduler_fire_claims.py`

- [ ] **Step 1: Write failing tests**

Test two concurrent claim attempts for the same `(task_id, fire_key)` return one winner and one existing claim. Test `_execute_task_internal` exits before dispatch when claim is not owned.

- [ ] **Step 2: Run RED**

Run: `timeout 60 uv run pytest tests/unit/master/test_scheduler_fire_claims.py -q`

Expected: failures because `fire_key` fields and claim service do not exist.

- [ ] **Step 3: Implement claim model and service**

Add `fire_key`, `lease_owner`, `lease_token`, `leased_until`, `attempt`, and `dispatch_message_id` fields. Add unique constraint on `(task_id, fire_key)`. Use DB insert/update as the correctness gate before Redis dispatch.

- [ ] **Step 4: Run GREEN**

Run: `timeout 60 uv run pytest tests/unit/master/test_scheduler_fire_claims.py -q`

Expected: all tests pass.

### Task 5: Result Write Before Task ACK

**Files:**
- Modify: `services/worker/src/antcode_worker/engine/engine.py`
- Test: `tests/unit/worker/test_engine_result_ack.py`

- [ ] **Step 1: Write failing tests**

Test `_report_result` does not call `ack_task` when `report_result` returns `False`, and still removes local state only after the correct behavior is complete.

- [ ] **Step 2: Run RED**

Run: `timeout 60 uv run pytest tests/unit/worker/test_engine_result_ack.py -q`

Expected: failure because current code ACKs even after result report failure.

- [ ] **Step 3: Implement ACK guard**

Only ACK when result report succeeds. On failure, leave the receipt unacked and keep enough local state for retry/recovery.

- [ ] **Step 4: Run GREEN**

Run: `timeout 60 uv run pytest tests/unit/worker/test_engine_result_ack.py -q`

Expected: all tests pass.

### Task 6: Focused Verification

**Files:**
- Modify only files touched by Tasks 1-5 if verification exposes defects.

- [ ] **Step 1: Run static checks**

Run: `timeout 60 uv run ruff check packages/antcode_core services/master services/web_api services/worker tests/unit/core tests/unit/master tests/unit/web_api tests/unit/worker`

Expected: no new lint errors from touched files.

- [ ] **Step 2: Run focused tests**

Run: `timeout 60 uv run pytest tests/unit/core/test_deployment_profile_config.py tests/unit/web_api/test_lifespan_production_startup.py tests/unit/core/test_redis_leases.py tests/unit/master/test_scheduler_fire_claims.py tests/unit/worker/test_engine_result_ack.py -q`

Expected: all focused tests pass.

- [ ] **Step 3: Review diff**

Run: `git diff -- packages/antcode_core services/master services/web_api services/worker tests docs/superpowers/plans/2026-04-28-control-plane-refactor.md`

Expected: only control-plane refactor changes appear.
