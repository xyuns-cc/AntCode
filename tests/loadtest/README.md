# AntCode guarded load tests

These tests are inert by default. `pytest tests/loadtest` runs only local guard and
metrics checks; the ten external scenarios are deselected.

## Safety contract

- External traffic requires both `--run-loadtests` and
  `ANTCODE_LOADTEST_CONFIRM=READ_ONLY|FULL`.
- `READ_ONLY` selects log and Worker observation scenarios only.
- `FULL` also selects task creation, dispatch, and backlog scenarios.
- `ANTCODE_LOADTEST_REDIS_URL` must use a non-zero database. Before any external
  scenario runs, the tool connects to it and verifies an exact target binding
  marker. An arbitrary, unreachable, or mismatched Redis URL fails closed.
- The token file must be a regular owner-only (`0600`) file. Put one bearer token
  per line. Tokens are round-robin selected by measured requests. Every token
  must be authorized for every configured project, Worker, and run. Cleanup uses
  the first token, which must be able to delete every task created by the run.
- Use a dedicated load project and Worker. Task scenarios delete every task they
  create and fail if cleanup is incomplete.
- Worker churn is externally orchestrated. The test only observes API state and
  requires a real `online -> offline -> online` transition.

## Required configuration

```text
ANTCODE_LOADTEST_CONFIRM=READ_ONLY|FULL
ANTCODE_LOADTEST_BASE_URL=http://target:port
ANTCODE_LOADTEST_REDIS_URL=redis://:password@redis-host:6379/14
ANTCODE_LOADTEST_REDIS_BINDING_KEY=antcode:loadtest:binding:acceptance
ANTCODE_LOADTEST_TOKEN_FILE=/secure/path/load-tokens
ANTCODE_LOADTEST_STAGE=VUS:QPS:DURATION_SECONDS
```

Write scenarios additionally require `ANTCODE_LOADTEST_PROJECT_ID` and
`ANTCODE_LOADTEST_WORKER_ID`. Log scenarios require comma-separated
`ANTCODE_LOADTEST_RUN_IDS`. Churn requires
`ANTCODE_LOADTEST_CHURN_WORKER_IDS` and a separate process that restarts those
Workers while the scenario runs.

The Redis marker is an explicit operator binding because the Web API does not
expose its middleware configuration. Generate and install the exact value before
the run; it includes the base URL, confirmation mode, project, and Worker:

```bash
BINDING_VALUE="$(uv run python -m tests.loadtest.tool.binding \
  --base-url "$ANTCODE_LOADTEST_BASE_URL" \
  --confirmation "$ANTCODE_LOADTEST_CONFIRM" \
  --project-id "$ANTCODE_LOADTEST_PROJECT_ID" \
  --worker-id "$ANTCODE_LOADTEST_WORKER_ID")"
redis-cli -u "$ANTCODE_LOADTEST_REDIS_URL" SET \
  "$ANTCODE_LOADTEST_REDIS_BINDING_KEY" "$BINDING_VALUE"
```

This verifies deliberate binding to the configured Redis database. It does not
claim that the Web API exposes or proves its private Redis connection settings.

Use one administrative token for the three write scenarios. For SSE stream loads
above the configured per-user connection limit, run the log scenario separately
with tokens for distinct authorized users; multiple sessions for the same user do
not increase the per-user limit.

## Coverage boundary

- `task-submission`, `task-dispatch`, and `backlog-trigger` exercise real task
  creation, scheduling, Worker execution, terminal-state recovery, and cleanup.
- `trigger-dedup` aims every virtual user at one task. `task-dispatch`
  deliberately triggers each task once to avoid the dedup lock, so without this
  scenario the most common production race — the same task triggered
  concurrently — is never exercised. It requires exactly one accepted trigger,
  only `200` / `409` to be observed, and a **measured peak of at least two
  simultaneously in-flight triggers**. Status codes alone cannot prove a race:
  the trigger dedup lock is a Redis key with a TTL, so serially released
  requests collect `409`s too. The scenario therefore releases its triggers all
  at once instead of at `1/QPS`; VUS is its concurrency, and `QPS * duration`
  is only the total trigger count.
- `worker-churn` observes a real externally driven restart. A Worker is only
  reported offline after `WORKER_HEARTBEAT_TIMEOUT` (60s by default) elapses
  without a heartbeat, so the stage duration must cover restart plus that
  timeout plus recovery — roughly 150s or more. A short duration fails the
  scenario (`did not transition online/offline/online`); it does not pass
  quietly.
- Dispatch correctness is asserted, not just throughput: every scenario that
  waits for runs reads a page of run history and fails if a `retry_count=0`
  task produced more than one run. Reading only the latest run cannot see a
  duplicate dispatch, because the newest of the two duplicates is also
  `success`.
- `sse-log-history`, `http-log-readers`, and `log-archive-download` are
  retained-log read workloads. They do not generate realtime log ingest traffic.
- `worker-inventory`, `worker-heartbeat`, and `worker-churn` load and observe the
  Web API control plane. They do not create Worker transport connections or send
  Worker heartbeat writes. Churn still requires a separate real Worker restart
  process.

Every configuration value except `ANTCODE_LOADTEST_CONFIRM` can also be supplied
as a `--loadtest-*` pytest option. The environment confirmation is deliberately
mandatory so a saved command cannot execute on its own.

## Execution

```bash
pytest tests/loadtest --run-loadtests -m loadtest_scenario -q -s
```

Requests are released at `1/QPS`, so the number of in-flight requests is about
`QPS * per-request latency`, not VUS. Every scenario reports the measured
`peak_concurrency`; check it before believing a result describes concurrent
traffic. The achievable QPS is also bounded by the global API rate limit
(`RATE_LIMIT_CALLS` / `RATE_LIMIT_PERIOD`, 1000 per 60s per client IP by
default). Write scenarios amplify that budget: setup, measured request,
post-run verification, and cleanup all count, and cleanup paces itself at one
request per 100ms.

The common assertion checks request count, achieved QPS, P50/P95/P99 latency,
status-code distribution, 5xx count, and total error rate. Thresholds are
configurable with `ANTCODE_LOADTEST_MAX_P50_MS`,
`ANTCODE_LOADTEST_MAX_P95_MS`, `ANTCODE_LOADTEST_MAX_P99_MS`,
`ANTCODE_LOADTEST_MAX_ERROR_RATE`, and `ANTCODE_LOADTEST_MIN_QPS_RATIO`.
Each scenario emits one `ANTCODE_LOADTEST_RESULT` JSON line. Keep `-s` enabled
and redirect or tee stdout when a durable result artifact is required.
