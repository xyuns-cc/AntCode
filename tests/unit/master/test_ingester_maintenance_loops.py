import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from antcode_core.domain.models.enums import BatchStatus
from antcode_master.ingester.alert_check_loop import AlertCheckLoop
from antcode_master.ingester.artifact_cleanup_loop import ArtifactCleanupLoop
from antcode_master.ingester.crawl_batch_status_loop import CrawlBatchStatusLoop

alert_module = importlib.import_module("antcode_master.ingester.alert_check_loop")
artifact_module = importlib.import_module("antcode_master.ingester.artifact_cleanup_loop")
batch_module = importlib.import_module("antcode_master.ingester.crawl_batch_status_loop")
project_module = importlib.import_module("antcode_core.domain.models.project")
progress_module = importlib.import_module("antcode_core.application.services.crawl.progress_service")


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def only(self, *_fields):
        return self

    async def all(self):
        return self.rows


class _Model:
    rows = []
    filters = []

    @classmethod
    def filter(cls, **criteria):
        cls.filters.append(criteria)
        return _Query(cls.rows)


class _Metrics:
    def __init__(self, failing=None):
        self.failing = failing
        self.checked = []

    async def check_alerts(self, project_id):
        self.checked.append(project_id)
        if project_id == self.failing:
            raise RuntimeError("metrics unavailable")


class _Cleanup:
    def __init__(self, events, error=None):
        self.events = events
        self.error = error

    async def cleanup_now(self):
        self.events.append("cleanup")
        if self.error:
            raise self.error


class _Progress:
    def __init__(self, error=None):
        self.error = error
        self.updates = []

    async def sync_progress_counters(self, **update):
        if self.error:
            raise self.error
        self.updates.append(update)


class _BatchQuery:
    async def update(self, **updates):
        _BatchModel.updates.append(updates)
        return _BatchModel.update_result


class _BatchModel:
    update_result = 1
    updates = []
    filters = []

    @classmethod
    def reset(cls, *, update_result=1):
        cls.update_result = update_result
        cls.updates = []
        cls.filters = []

    @classmethod
    def filter(cls, **criteria):
        cls.filters.append(criteria)
        return _BatchQuery()


class _LogRecorder:
    def __init__(self):
        self.warnings = []
        self.exceptions = []

    def warning(self, message):
        self.warnings.append(message)

    def exception(self, message):
        self.exceptions.append(message)

    def info(self, _message):
        return None


def _batch(*, seed_count=2):
    return SimpleNamespace(
        id=1,
        public_id="batch-1",
        project_id=9,
        seed_urls=[f"https://example.test/{index}" for index in range(seed_count)],
        started_at=datetime.now(UTC) - timedelta(hours=1),
        status=BatchStatus.RUNNING.value,
        completed_at=None,
    )


@pytest.mark.asyncio
async def test_alert_tick_deduplicates_projects_and_continues_after_failure(monkeypatch):
    batches = [SimpleNamespace(project_id=value) for value in (1, 1, 2, 3)]
    projects = [SimpleNamespace(id=1, public_id="p-1"), SimpleNamespace(id=2, public_id="p-2")]
    metrics = _Metrics(failing="p-1")
    alert_batches = type("AlertBatches", (_Model,), {"rows": batches, "filters": []})
    alert_projects = type("AlertProjects", (_Model,), {"rows": projects, "filters": []})
    monkeypatch.setattr(alert_module, "CrawlBatch", alert_batches)
    monkeypatch.setattr(project_module, "Project", alert_projects)
    monkeypatch.setattr(alert_module, "crawl_metrics_service", metrics)

    await AlertCheckLoop()._tick()

    assert metrics.checked == ["p-1", "p-2"]
    assert alert_batches.filters[0]["status__in"] == ["running", "paused"]


@pytest.mark.asyncio
async def test_alert_attempt_limit_holds_when_every_dependency_call_fails(monkeypatch):
    batches = [SimpleNamespace(project_id=index) for index in range(1, 503)]
    projects = [SimpleNamespace(id=index, public_id=f"p-{index}") for index in range(1, 503)]
    metrics = _Metrics()

    async def always_fail(project_id):
        metrics.checked.append(project_id)
        raise RuntimeError("metrics unavailable")

    metrics.check_alerts = always_fail
    batch_model = type("LimitBatches", (_Model,), {"rows": batches, "filters": []})
    project_model = type("LimitProjects", (_Model,), {"rows": projects, "filters": []})
    monkeypatch.setattr(alert_module, "CrawlBatch", batch_model)
    monkeypatch.setattr(project_module, "Project", project_model)
    monkeypatch.setattr(alert_module, "crawl_metrics_service", metrics)

    await AlertCheckLoop()._tick()

    assert len(metrics.checked) == alert_module.MAX_PROJECTS_PER_TICK


@pytest.mark.asyncio
async def test_artifact_tick_runs_cleanup_before_orphan_check(monkeypatch):
    events = []
    loop = ArtifactCleanupLoop()

    async def check_orphans():
        events.append("orphans")

    loop._check_orphans = check_orphans
    monkeypatch.setattr(artifact_module, "artifact_cleanup_service", _Cleanup(events))

    await loop._tick()

    assert events == ["cleanup", "orphans"]


@pytest.mark.asyncio
async def test_artifact_tick_propagates_primary_cleanup_failure(monkeypatch):
    failure = RuntimeError("postgres down")
    monkeypatch.setattr(artifact_module, "artifact_cleanup_service", _Cleanup([], failure))

    with pytest.raises(RuntimeError, match="postgres down"):
        await ArtifactCleanupLoop()._tick()


@pytest.mark.asyncio
async def test_artifact_orphan_query_failure_is_recorded(monkeypatch):
    logs = _LogRecorder()

    class FailingConnection:
        async def execute_query(self, _sql, _params=None):
            raise RuntimeError("postgres down")

    connections = SimpleNamespace(get=lambda _name: FailingConnection())
    monkeypatch.setattr("tortoise.connections", connections)
    monkeypatch.setattr(artifact_module, "logger", logs)

    await ArtifactCleanupLoop()._check_orphans()

    assert "[orphan-check] 查询失败: postgres down" in logs.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stat", "expected"),
    [
        ({"total": 2, "success": 2, "failed": 0, "cancelled": 0, "active": 0}, "completed"),
        ({"total": 2, "success": 0, "failed": 0, "cancelled": 2, "active": 0}, "cancelled"),
        ({"total": 2, "success": 1, "failed": 1, "cancelled": 0, "active": 0}, "failed"),
    ],
)
async def test_batch_terminal_state_is_persisted_with_cas(monkeypatch, stat, expected):
    _BatchModel.reset()
    monkeypatch.setattr(batch_module, "CrawlBatch", _BatchModel)
    batch = _batch()

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert _BatchModel.filters == [{"id": 1, "status": "running"}]
    assert _BatchModel.updates[0]["status"] == expected
    assert batch.status == expected
    assert batch.completed_at.tzinfo == UTC


@pytest.mark.asyncio
async def test_progress_failure_is_logged_without_blocking_terminal_state(monkeypatch):
    _BatchModel.reset()
    logs = _LogRecorder()
    progress = _Progress(RuntimeError("redis down"))
    monkeypatch.setattr(batch_module, "CrawlBatch", _BatchModel)
    monkeypatch.setattr(progress_module, "crawl_progress_service", progress)
    monkeypatch.setattr(batch_module, "logger", logs)
    stat = {"total": 2, "success": 2, "failed": 0, "cancelled": 0, "active": 0}
    batch = _batch()

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat, "project-public")

    assert batch.status == BatchStatus.COMPLETED.value
    assert "batch 进度同步失败" in logs.warnings[0]


@pytest.mark.asyncio
async def test_batch_cas_lost_race_does_not_mutate_stale_object(monkeypatch):
    _BatchModel.reset(update_result=0)
    monkeypatch.setattr(batch_module, "CrawlBatch", _BatchModel)
    batch = _batch()
    stat = {"total": 2, "success": 2, "failed": 0, "cancelled": 0, "active": 0}

    await CrawlBatchStatusLoop()._reconcile_batch(batch, stat)

    assert batch.status == BatchStatus.RUNNING.value
    assert batch.completed_at is None


@pytest.mark.asyncio
async def test_batch_tick_isolates_one_batch_failure(monkeypatch):
    batches = [_batch(), SimpleNamespace(**{**_batch().__dict__, "id": 2, "public_id": "batch-2"})]
    batch_model = type("TickBatches", (_Model,), {"rows": batches, "filters": []})
    loop = CrawlBatchStatusLoop()
    processed = []

    async def reconcile(batch, _stat, _project_id):
        processed.append(batch.public_id)
        if batch.public_id == "batch-1":
            raise RuntimeError("bad row")

    async def stats(_batch_ids):
        return {}

    async def projects(_project_ids):
        return {}

    monkeypatch.setattr(batch_module, "CrawlBatch", batch_model)
    loop._fetch_batch_stats = stats
    loop._fetch_project_public_ids = projects
    loop._reconcile_batch = reconcile

    await loop._tick()

    assert processed == ["batch-1", "batch-2"]
