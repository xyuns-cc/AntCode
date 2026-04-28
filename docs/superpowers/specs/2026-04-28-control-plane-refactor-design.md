# Control Plane Refactor Design

## Goal

Make AntCode production single-node deployment and distributed deployment use the same control-plane semantics. A production single-node deployment is a distributed topology with one replica of each service, not a SQLite/local/memory shortcut.

## Scope

This design covers the first refactor phase:

- Deployment profiles and production fail-fast validation.
- Database initialization and migration ownership.
- Redis control-plane usage.
- Distributed lock and fencing semantics.
- Master scheduling idempotency.
- Task dispatch/result acknowledgement correctness.

The next full-system refactor will build on this phase for log storage, code artifact storage, Worker isolation, and observability. Those areas should not be redesigned until control-plane correctness is stable.

## Deployment Profiles

AntCode has three explicit profiles:

- `dev`: local development only. SQLite, local storage, and memory-only helpers are allowed.
- `single-node`: production on one host. Requires MySQL or PostgreSQL, Redis, and S3-compatible object storage.
- `distributed`: production across multiple hosts. Requires the same dependencies as `single-node`.

`single-node` and `distributed` share the same runtime behavior. The only difference is service replica count and network placement.

Production profile validation fails startup when any required dependency is missing:

- `DATABASE_URL` is empty or points to SQLite.
- `REDIS_URL` is empty or unreachable.
- `FILE_STORAGE_BACKEND` is not `s3`.
- `LOG_STORAGE_BACKEND` is not a production-supported backend.
- Required auth and encryption secrets are missing.

No production service should continue with partial functionality after a required dependency fails.

## Database Ownership

The database is the source of truth for tasks, runs, workers, schedules, leases, and durable state transitions.

Production services do not create or patch schema on startup. Startup performs only:

- database connectivity check,
- migration-version check,
- required index/constraint check.

Schema changes are applied by an explicit migration command. This prevents multiple Web API or Master replicas from concurrently changing tables.

SQLite support remains limited to `dev`. Production tests should use MySQL/PostgreSQL because lock, transaction, and uniqueness behavior must match production.

## Redis Ownership

Redis is a control-plane transport and coordination system, not a long-term source of truth.

Redis owns:

- Worker task ready streams,
- result streams,
- hot log streams,
- Worker control streams,
- short-lived leases,
- fencing token allocation,
- idempotency assist keys where useful.

Redis does not own durable task status, durable run history, project metadata, or final log archives.

## Lock And Fencing Model

Redis locks are short-lived leases. They reduce duplicate work but do not define final correctness.

Each lease value includes:

- `owner_id`: stable process identity,
- `token`: monotonically increasing fencing token,
- `expires_at`: lease expiration timestamp.

Lease acquisition and renewal are done with Lua scripts so ownership checks and expiry updates are atomic. Renewal failure immediately marks the local process as not eligible to schedule or mutate control-plane state.

Every DB write made under a leader or scheduler lease includes a fencing condition. Stale leaders cannot overwrite newer decisions. Redis fencing token allocation remains monotonic, while DB state transitions enforce token validity for durable correctness.

## Scheduler Model

The scheduler moves from memory-first execution to DB-first claiming.

For each scheduled trigger, Master computes a stable `fire_key`:

- cron: task id plus scheduled fire timestamp,
- interval: task id plus interval window start,
- one-time/date: task id plus scheduled timestamp,
- manual trigger: generated request id.

`task_runs` receives fields for `fire_key`, `lease_owner`, `lease_token`, `leased_until`, and attempt metadata. The database has a unique constraint on `(task_id, fire_key)`.

When multiple Master replicas observe the same schedule, each attempts to create or claim the same fire. Only one succeeds. Leader election remains useful for efficiency, but duplicate prevention depends on database constraints and atomic transitions.

The state transition sequence is:

1. create or claim due fire in DB,
2. create `TaskRun` with dispatch state,
3. write task message to Redis ready stream,
4. mark dispatch accepted with Redis message id,
5. reconcile expired dispatch leases from DB.

Master loops that mutate durable state must pass through the same claim and fencing path.

## Dispatch And Result Semantics

Task execution uses at-least-once delivery with idempotent result handling.

Worker must not ACK a task before result durability is guaranteed. Result handling uses one of two acceptable models:

- Worker writes result to Redis result stream, waits for success, then ACKs task receipt.
- Worker writes result to Redis result stream without ACK; Master updates DB and emits an explicit completion ACK command. This model is more complex and should only be used if Redis pending management requires it.

The first model is the default. If result stream write fails, the task remains pending and is reclaimed or retried explicitly.

Master result consumption is idempotent by `run_id`. Repeated result messages update the same run only when the transition is valid. Invalid regressions fail loudly in logs and tests.

## Reconciliation

Reconciliation is DB-driven:

- expired dispatch leases are returned to queued or failed state,
- expired running leases are marked timed out or retryable,
- Worker offline status comes from heartbeat freshness plus DB state,
- Redis pending entries are reclaimed only when DB says the run is still active.

Reconcile actions use the same fencing and transition checks as normal scheduling.

## Configuration Cleanup

Remove silent production fallbacks:

- no Redis connection warning followed by degraded startup in production,
- no API-key format fallback for Gateway authentication,
- no `file://` pseudo upload path in production log archival,
- no local storage backend in production profiles,
- no schema auto-repair on service startup.

Development fallbacks are allowed only under `ANTCODE_PROFILE=dev` and must be explicit in logs.

## Testing Strategy

Tests are split by risk:

- Unit tests for profile validation and dependency requirements.
- Unit tests for Redis lease Lua scripts and fencing behavior using mocked Redis.
- Database tests for `(task_id, fire_key)` uniqueness and valid state transitions.
- Scheduler concurrency tests that simulate two Master instances trying to claim the same fire.
- Worker transport tests proving result write failure does not ACK the task.
- Integration tests using MySQL/PostgreSQL and Redis for single-node production profile.

Backend test commands must use a 60-second timeout.

## Acceptance Criteria

The phase is complete when:

- `single-node` and `distributed` profiles reject SQLite/local/memory production configuration.
- Production service startup fails when DB, Redis, or required storage is unavailable.
- Web API and Master no longer generate or patch schema on startup in production.
- Two Master instances cannot create duplicate runs for the same scheduled fire.
- Losing a Redis lease prevents further scheduler writes from that process.
- Worker result reporting failure leaves the task unacked and recoverable.
- Existing Direct and Gateway Worker transports still run through the unified Redis control-plane keys.

## Full Refactor Follow-Up Boundary

After this phase lands, the full-system refactor should address:

- immutable project artifact storage,
- streaming-safe S3 uploads/downloads,
- log hot/archive split with no S3 append pattern,
- Worker execution isolation,
- production observability and dead-letter operations.

Those changes should depend on the control-plane contracts defined here instead of adding more compatibility paths.
