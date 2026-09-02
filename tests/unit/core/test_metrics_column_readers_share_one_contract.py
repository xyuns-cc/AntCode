"""``workers.metrics`` 的剩余读者收敛到 ``persisted_worker_metrics``，各说各话的口径清零。

同一列 jsonb 已经因为"每处读法各有各的口径"酿成三个缺陷（87cf267 读回侧 500、0ee5631
派发侧 ValueError + 静默污染、e24874c 写入侧 TypeError 导致那台机器不再自愈）。契约建立
之后仍有五处没走它，坏列在它们那里分成两类后果：

- **抛**：``spider_stats_service`` 两处与 ``worker_stats_service.get_aggregate_stats``
  把整列直接 ``.get``，list/str 一律 ``AttributeError``。三个管理端接口
  （``/workers/stats``、``/workers/stats/spider``、``/workers/{id}/stats/spider``）
  整页 500，同一批里的好机器一起陪葬；
- **静默**：``get_queue_status`` 与 ``GET /workers/{id}/resources`` 各自就地
  ``isinstance(..., dict) else {}``，一声不响回一份全 0 —— 与一台真空闲的机器逐字节相同，
  而 ``max_concurrent_tasks=0`` 读起来还像"这台什么都跑不了"，运维查不到任何线索。

所以这里的判据分两种：会抛的那三处认"降级之后同批的好机器仍算得对"，静默的那两处认
"那条点名 WARNING 出现了"——它们的返回值修复前后本来就一样，只认返回值会是假绿。

**证伪方式**：把任意一处的 ``persisted_worker_metrics(worker)`` 换回 ``worker.metrics``
（``get_queue_status`` / ``get_worker_resources`` 换回 ``... if isinstance(..., dict) else {}``），
对应那组变红。删 ``persisted_worker_metrics`` 里的 WARNING，第 2 组全红。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.worker_dispatcher import WorkerTaskDispatcher
from loguru import logger

from tests.unit.core.broken_metrics_column_support import (
    COLUMN_ALARM,
    LIST_COLUMN,
    PAIRABLE_COLUMN,
    STR_COLUMN,
    capture_logs,
    worker,
)

ROOT = Path(__file__).resolve().parents[3]

# 三种真实见过的坏形状：前两种从前抛，第三种从前在别处被拆成键值对。
BROKEN_COLUMNS = [LIST_COLUMN, STR_COLUMN, PAIRABLE_COLUMN]

HEALTHY_CPU = 12
HEALTHY_TASKS = 3
HEALTHY_RUNNING = 1
HEALTHY_SLOTS = 4
HEALTHY_QUEUED = 2
HEALTHY_REQUESTS = 7
HEALTHY_RESPONSES = 5
WORKERS_IN_BATCH = 2

HEALTHY_COLUMN = {
    "cpu": HEALTHY_CPU,
    "taskCount": HEALTHY_TASKS,
    "runningTasks": HEALTHY_RUNNING,
    "maxConcurrentTasks": HEALTHY_SLOTS,
    "queuedTasks": HEALTHY_QUEUED,
    "spider_stats": {"request_count": HEALTHY_REQUESTS, "response_count": HEALTHY_RESPONSES},
}

# 允许直接读这一列的两个模块：契约本身，以及回显侧——它必须把"空列"和"坏列"分别
# 翻成不同的响应体（WorkerSnapshotError），而契约把两者都收敛成 None，接不住。
COLUMN_READERS = {
    "packages/antcode_core/src/antcode_core/application/services/workers/worker_resource_probe.py",
    "services/web_api/src/antcode_web_api/routes/v1/worker_snapshot_readback.py",
}


class _Rows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def filter(self, **_kwargs):
        return self

    async def all(self):
        return list(self._rows)

    async def first(self):
        return self._rows[0] if self._rows else None


def _service_module(name: str):
    """按模块全名取：``workers/__init__.py`` 把同名属性重绑成了服务**实例**。"""
    return importlib.import_module(f"antcode_core.application.services.workers.{name}")


def _install_rows(monkeypatch, name: str, rows):
    module = _service_module(name)
    table = SimpleNamespace(filter=lambda **_kwargs: _Rows(rows), all=lambda: _Rows(rows).all())
    monkeypatch.setattr(module, "Worker", table)
    return module


def _install_resources_route(monkeypatch, node):
    import antcode_web_api.routes.v1.workers_resources as module

    async def _one(_worker_id):
        return node

    async def _admin(_current_user):
        return SimpleNamespace(is_admin=True)

    monkeypatch.setattr(module, "worker_service", SimpleNamespace(get_worker_by_id=_one))
    monkeypatch.setattr(module, "_require_admin", _admin)
    return module


async def _resource_stats(module) -> dict:
    payload = await module.get_worker_resources("worker-1", SimpleNamespace(user_id=1))
    return payload.data["resource_stats"]


def _mixed_batch(column):
    return [worker(1, "mn-broken", column), worker(2, "mn-healthy", HEALTHY_COLUMN)]


# --------------------------------------------------------------------------------------
# 1. 会抛的三处：坏列降级成"这台没上报过"，同批的好机器照常算得对
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", BROKEN_COLUMNS)
@pytest.mark.asyncio
async def test_one_broken_column_does_not_erase_the_cluster_spider_totals(monkeypatch, column) -> None:
    """``GET /workers/stats/spider``：从前一台坏列就是整页 500，好机器的读数一起丢。"""
    module = _install_rows(monkeypatch, "spider_stats_service", _mixed_batch(column))

    stats = await module.spider_stats_service.get_cluster_spider_stats()

    assert stats["totalRequests"] == HEALTHY_REQUESTS
    assert stats["totalResponses"] == HEALTHY_RESPONSES
    assert stats["workerCount"] == WORKERS_IN_BATCH


@pytest.mark.parametrize("column", BROKEN_COLUMNS)
@pytest.mark.asyncio
async def test_a_broken_column_reads_back_as_no_spider_stats_for_that_worker(monkeypatch, column) -> None:
    """``GET /workers/{id}/stats/spider``：单台查询也不许 500，如实为"没有统计"。"""
    module = _install_rows(monkeypatch, "spider_stats_service", [worker(1, "mn-broken", column)])

    summary = await module.spider_stats_service.get_worker_spider_stats(1)

    assert summary.requestCount == 0


@pytest.mark.parametrize("column", BROKEN_COLUMNS)
@pytest.mark.asyncio
async def test_a_broken_column_leaves_the_aggregate_stats_of_the_healthy_worker_intact(monkeypatch, column) -> None:
    """``GET /workers/stats``：坏列那台被排除在**分母**外，而不是按 cpu=0 拉低均值。

    ``avgCpu`` 是这一组真正的判据：算成 ``HEALTHY_CPU / 2`` 就说明坏列被当成了一份
    合法的空指标混进统计——那是 0ee5631 静默污染的同一个形状。
    """
    module = _install_rows(monkeypatch, "worker_stats_service", _mixed_batch(column))

    stats = await module.worker_stats_service.get_aggregate_stats()

    assert stats.totalWorkers == WORKERS_IN_BATCH
    assert stats.totalTasks == HEALTHY_TASKS
    assert stats.totalRequests == HEALTHY_REQUESTS
    assert stats.avgCpu == HEALTHY_CPU


# --------------------------------------------------------------------------------------
# 2. 静默的两处：返回值本来就没变，判据只能是那条点名 WARNING
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", BROKEN_COLUMNS)
@pytest.mark.asyncio
async def test_queue_status_names_the_broken_column_instead_of_reporting_zero_slots(column) -> None:
    """全 0 的队列状态与一台真空闲的机器逐字节相同；不出声就等于把这一列的问题藏起来。"""
    node = worker(1, "mn-broken", column)

    records, sink_id = capture_logs()
    try:
        queue_status = await WorkerTaskDispatcher().get_queue_status(node)
    finally:
        logger.remove(sink_id)

    warnings = records.text("WARNING")
    assert COLUMN_ALARM in warnings
    assert "mn-broken" in warnings
    assert queue_status["max_concurrent_tasks"] == 0


@pytest.mark.parametrize("column", BROKEN_COLUMNS)
@pytest.mark.asyncio
async def test_the_resources_page_names_the_broken_column_behind_its_zeroed_readings(monkeypatch, column) -> None:
    """``GET /workers/{id}/resources`` 的全 0 面板同理：页面看不出来，日志必须看得出来。"""
    module = _install_resources_route(monkeypatch, worker(1, "mn-broken", column))

    records, sink_id = capture_logs()
    try:
        resource_stats = await _resource_stats(module)
    finally:
        logger.remove(sink_id)

    warnings = records.text("WARNING")
    assert COLUMN_ALARM in warnings
    assert "mn-broken" in warnings
    assert resource_stats["cpu_percent"] == 0.0


# --------------------------------------------------------------------------------------
# 3. 控制组：列正常时五处口径一个不变，且不许冒出"列坏了"这句话
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthy_columns_keep_every_reading_and_raise_no_column_alarm(monkeypatch) -> None:
    """**控制组**：修复前后都绿。它钉的是"收敛没有顺手改掉正常路径的读数"。"""
    node = worker(1, "mn-healthy", HEALTHY_COLUMN)
    spider = _install_rows(monkeypatch, "spider_stats_service", [node])
    stats = _install_rows(monkeypatch, "worker_stats_service", [node])
    resources = _install_resources_route(monkeypatch, node)

    records, sink_id = capture_logs()
    try:
        queue_status = await WorkerTaskDispatcher().get_queue_status(node)
        summary = await spider.spider_stats_service.get_worker_spider_stats(1)
        cluster = await spider.spider_stats_service.get_cluster_spider_stats()
        aggregate = await stats.worker_stats_service.get_aggregate_stats()
        resource_stats = await _resource_stats(resources)
    finally:
        logger.remove(sink_id)

    assert queue_status == {
        "queued_tasks": HEALTHY_QUEUED,
        "running_tasks": HEALTHY_RUNNING,
        "max_concurrent_tasks": HEALTHY_SLOTS,
    }
    assert summary.requestCount == HEALTHY_REQUESTS
    assert cluster["totalResponses"] == HEALTHY_RESPONSES
    assert aggregate.avgCpu == HEALTHY_CPU
    assert resource_stats["cpu_percent"] == float(HEALTHY_CPU)
    assert resource_stats["running_tasks"] == HEALTHY_RUNNING
    assert COLUMN_ALARM not in records.text()


# --------------------------------------------------------------------------------------
# 4. 不许再长出第六个读者
# --------------------------------------------------------------------------------------


def _direct_column_readers() -> set[str]:
    found: set[str] = set()
    for base in ("packages", "services"):
        for path in (ROOT / base).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if _is_column_read(node):
                    found.add(path.relative_to(ROOT).as_posix())
    return found


def _is_column_read(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "metrics"
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
        and node.value.id == "worker"
    )


def test_only_the_contract_and_the_readback_touch_the_column_directly() -> None:
    """新读者一律走 ``persisted_worker_metrics``；这条红了说明这一列又多了一种口径。

    这一列已经修过四轮，每一轮的根因都是"又有一处自己读"。允许直读的只剩两处，理由
    写在 ``COLUMN_READERS`` 上方。
    """
    assert _direct_column_readers() == COLUMN_READERS
