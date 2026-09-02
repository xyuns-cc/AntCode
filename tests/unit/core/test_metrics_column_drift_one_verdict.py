"""``workers.metrics`` 键集漂移时，``GET /workers`` 与 ``GET /workers/stats`` 必须同判。

这一列的第五处口径分叉，也是前四轮（87cf267 / 0ee5631 / e24874c / f5a2e22）之后剩下的
最后一处：``persisted_worker_metrics`` 只保证这一列是个 JSON 对象，而读回侧用
``WorkerMetrics``（``extra="forbid"``）解同一列。于是"什么算有指标"两边不是一个判定，
一台机器同时是"页面上没有指标"和"集群统计里的一个样本"。真机（一次性 PG，真 jsonb
往返）实测的改前读数，同批永远搭一台 cpu=12 的好机器：

===================== ================================== ==============
坏法                   GET /workers                       avgCpu
===================== ================================== ==============
多一个 ``gpuUtil``     metrics null + field_mismatch      50.0（88 被算进去）
cpu 改名 ``cpuUsage``  metrics null + field_mismatch      6.0（缺键折算成 0）
``cpu: "abc"``         metrics null + field_mismatch      整个接口 TypeError → 500
从没上报（NULL）       metrics null，无 error             12.0（唯一本来就同判的）
===================== ================================== ==============

**证伪方式**：把 ``get_aggregate_stats`` 的 ``readable_worker_metrics`` 换回
``persisted_worker_metrics``（``.get("cpu", 0)`` 那一套），第 2、3 组按上表变红；删掉
``readable_worker_metrics`` 里的 WARNING，第 4 组变红。第 1 组钉的是两侧不许再分家，
它不认返回值大小，只认"两边对同一列的结论一样"。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.worker_resource_probe import readable_worker_metrics
from antcode_web_api.routes.v1.worker_snapshot_readback import read_worker_snapshot
from loguru import logger

from tests.unit.core.broken_metrics_column_support import (
    LIST_COLUMN,
    PAIRABLE_COLUMN,
    STR_COLUMN,
    capture_logs,
    worker,
)

HEALTHY_CPU = 12.0
HEALTHY_MEMORY = 40.0
HEALTHY_TASKS = 3
HEALTHY_RUNNING = 1
HEALTHY_PROJECTS = 2
DRIFT_CPU = 88.0
DRIFT_MEMORY = 90.0
DRIFT_TASKS = 7
RENAMED_CPU = 55.0
OUT_OF_RANGE_CPU = 150.0
WORKERS_IN_BATCH = 2
SPIDER_RESPONSES = 4
DRIFT_ALARM = "读不回 WorkerMetrics"

HEALTHY_COLUMN = {
    "cpu": HEALTHY_CPU,
    "memory": HEALTHY_MEMORY,
    "taskCount": HEALTHY_TASKS,
    "runningTasks": HEALTHY_RUNNING,
    "projectCount": HEALTHY_PROJECTS,
}

# Worker 二进制多报一个控制面 schema 没声明的键；cpu 本身是好的，均值被拉高。
DRIFT_EXTRA_KEY = {**HEALTHY_COLUMN, "cpu": DRIFT_CPU, "memory": DRIFT_MEMORY, "gpuUtil": DRIFT_CPU}
# Worker 把 cpu 改了名；``.get("cpu", 0)`` 拿到 0，均值被拉低。
DRIFT_RENAMED_KEY = {"cpuUsage": RENAMED_CPU, "memory": DRIFT_MEMORY, "taskCount": DRIFT_TASKS}
# 形状是 JSON 对象，取值却不是数：从前 ``total_cpu += "abc"`` 把整个接口打成 500。
DRIFT_BAD_TYPE = {"cpu": "abc", "memory": DRIFT_MEMORY}
# 取值越界：schema 有 le=100，形状检查看不出来。
DRIFT_OUT_OF_RANGE = {"cpu": OUT_OF_RANGE_CPU, "memory": DRIFT_MEMORY}

DRIFTED_COLUMNS = [DRIFT_EXTRA_KEY, DRIFT_RENAMED_KEY, DRIFT_BAD_TYPE, DRIFT_OUT_OF_RANGE]

# 两侧必须给出同一个结论的全部列形状：读得回来的、四种漂移的、三种不是 JSON 对象的、
# 以及两种"这台还没上报过"。
EVERY_COLUMN_SHAPE = [
    HEALTHY_COLUMN,
    *DRIFTED_COLUMNS,
    LIST_COLUMN,
    STR_COLUMN,
    PAIRABLE_COLUMN,
    None,
    {},
]


class _Rows:
    def __init__(self, rows) -> None:
        self._rows = rows

    async def all(self):
        return list(self._rows)


def _stats_module():
    """按模块全名取：``workers/__init__.py`` 把同名属性重绑成了服务**实例**，
    ``from ... import worker_stats_service`` 拿到的是实例，打桩会打到一个没有
    ``Worker`` 属性的对象上（本轮实测：AttributeError，不是假绿但同一个坑）。"""
    return importlib.import_module("antcode_core.application.services.workers.worker_stats_service")


def _batch_with(column):
    return [worker(1, "mn-drifted", column), worker(2, "mn-healthy", HEALTHY_COLUMN)]


async def _aggregate(monkeypatch, rows):
    module = _stats_module()
    monkeypatch.setattr(module, "Worker", SimpleNamespace(all=lambda: _Rows(rows).all()))
    return await module.worker_stats_service.get_aggregate_stats()


# --------------------------------------------------------------------------------------
# 1. 两侧同判：这一列不许再有第二种"什么算有指标"
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", EVERY_COLUMN_SHAPE)
def test_both_endpoints_agree_on_whether_this_worker_has_metrics(column) -> None:
    """``GET /workers`` 说没有指标的那台，集群统计里也必须不算有指标，反之亦然。

    这一条不看数值——它钉的就是判定本身。读回 schema 一改（加字段、放宽取值），两边同时
    跟着变；哪天又有人给聚合侧单独写一套判据，这条立刻红。
    """
    node = worker(1, "mn-under-test", column)

    listed = read_worker_snapshot(node).metrics
    aggregated = readable_worker_metrics(node)

    assert (listed is None) == (aggregated is None)


# --------------------------------------------------------------------------------------
# 2. 漂移那台退出统计：均值不再被一台"页面上写着没数据"的机器带偏
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", [DRIFT_EXTRA_KEY, DRIFT_RENAMED_KEY, DRIFT_OUT_OF_RANGE])
@pytest.mark.asyncio
async def test_a_drifted_worker_leaves_the_cluster_average_to_the_readable_ones(monkeypatch, column) -> None:
    """分子分母都不进：``avgCpu`` 恰是那台读得回来的机器的读数。

    改前三种漂移各带偏一个方向（88 拉高到 50、缺键折算成 0 拉低到 6、越界 150 拉高到 81），
    全等断言同时钉住正值与这三个错值。
    """
    stats = await _aggregate(monkeypatch, _batch_with(column))

    assert stats.totalWorkers == WORKERS_IN_BATCH
    assert stats.avgCpu == HEALTHY_CPU
    assert stats.avgMemory == HEALTHY_MEMORY
    assert stats.totalTasks == HEALTHY_TASKS
    assert stats.totalProjects == HEALTHY_PROJECTS


# --------------------------------------------------------------------------------------
# 3. 取值坏了不再掀掉整个接口
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_non_numeric_reading_no_longer_turns_the_whole_endpoint_into_a_500(monkeypatch) -> None:
    """``{"cpu": "abc"}`` 过得了形状检查，从前 ``total_cpu += "abc"`` 是 TypeError。

    同批的好机器一起陪葬，而 ``GET /workers`` 上这台只是自己那一列置空——同一份数据、
    两个接口，一个报得清清楚楚、一个整页 500。
    """
    stats = await _aggregate(monkeypatch, _batch_with(DRIFT_BAD_TYPE))

    assert stats.avgCpu == HEALTHY_CPU


# --------------------------------------------------------------------------------------
# 4. 退出统计必须出声：点名到机器和键
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", DRIFTED_COLUMNS)
@pytest.mark.asyncio
async def test_dropping_a_worker_from_the_cluster_average_names_it_and_the_offending_keys(monkeypatch, column) -> None:
    """少一台进分母在响应体里看不出来（``workers_with_metrics`` 不出网），日志必须看得出来。

    没有这条，"控制面 schema 与 Worker 二进制键集错配"就只剩一个悄悄变小的分母。
    """
    records, sink_id = capture_logs()
    try:
        await _aggregate(monkeypatch, _batch_with(column))
    finally:
        logger.remove(sink_id)

    warnings = records.text("WARNING")
    assert DRIFT_ALARM in warnings
    assert "mn-drifted" in warnings
    assert "mn-healthy" not in warnings


# --------------------------------------------------------------------------------------
# 5. 控制组：读得回来的与从没上报过的，读数与安静程度都不许变
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_worker_that_never_reported_is_excluded_quietly_and_the_readings_stand(monkeypatch) -> None:
    """**控制组**：修复前后都绿。

    空列本来就两侧同判（"这台还没上报过"），它不该被新的 WARNING 说成键集错配——那是
    对着一台正常的新机器喊狼来了。同时钉住好机器的读数没被这次收敛改掉。
    """
    batch = [worker(1, "mn-never", None), worker(2, "mn-healthy", HEALTHY_COLUMN)]

    records, sink_id = capture_logs()
    try:
        stats = await _aggregate(monkeypatch, batch)
    finally:
        logger.remove(sink_id)

    assert stats.totalWorkers == WORKERS_IN_BATCH
    assert stats.avgCpu == HEALTHY_CPU
    assert stats.totalTasks == HEALTHY_TASKS
    assert stats.runningTasks == HEALTHY_RUNNING
    assert DRIFT_ALARM not in records.text()


# --------------------------------------------------------------------------------------
# 6. 一台都读不回来时：null 是"没有数据"，0.0 是"整个集群闲着"
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", [*DRIFTED_COLUMNS, None])
@pytest.mark.asyncio
async def test_a_fully_unreadable_cluster_reports_no_average_instead_of_zero(monkeypatch, column) -> None:
    """把漂移那台退出统计,就让"全集群都读不回来"成了一条可达路径。

    分母为 0 时旧实现返回 ``0.0``——与下面那条"整个集群闲着"的读数逐字节相同,
    运维看到的都是"平均 CPU 0%"。机器还在（``totalWorkers`` 非 0）,只是没有一台
    的指标读得回来,这时唯一诚实的答案是 null。
    """
    batch = [worker(1, "mn-a", column), worker(2, "mn-b", column)]

    stats = await _aggregate(monkeypatch, batch)

    assert stats.totalWorkers == WORKERS_IN_BATCH
    assert stats.avgCpu is None
    assert stats.avgMemory is None


@pytest.mark.asyncio
async def test_a_genuinely_idle_cluster_still_reports_zero(monkeypatch) -> None:
    """**反判据**：读得回来的 0% 必须还是 0.0,不能被这次改动一起吞成 null。

    没有这条,把 ``avgCpu`` 一律改成 null 也能让上面那条变绿。
    """
    idle = {**HEALTHY_COLUMN, "cpu": 0.0, "memory": 0.0}
    batch = [worker(1, "mn-a", idle), worker(2, "mn-b", idle)]

    stats = await _aggregate(monkeypatch, batch)

    assert stats.avgCpu == 0.0
    assert stats.avgMemory == 0.0


# --------------------------------------------------------------------------------------
# 7. avgLatencyMs 是同一个形状：分母是"响应数"，不是"读得回指标的机器数"
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cluster_that_served_no_response_reports_no_latency(monkeypatch) -> None:
    """指标读得回来、但一条响应都没发生过时，平均延迟只能是 null。

    ``0.0`` 与下面"每条响应都是 0ms"逐字节相同——同 ``avgCpu`` 的坑，只是分母换成了
    ``total_responses``。这里的两台机器是完全健康的，不带 ``spider_stats``。
    """
    batch = [worker(1, "mn-a", HEALTHY_COLUMN), worker(2, "mn-b", HEALTHY_COLUMN)]

    stats = await _aggregate(monkeypatch, batch)

    assert stats.totalResponses == 0
    assert stats.avgLatencyMs is None


@pytest.mark.asyncio
async def test_real_zero_latency_responses_still_report_zero(monkeypatch) -> None:
    """**反判据**：真的发生过响应且延迟为 0，必须还是 0.0，不能一起吞成 null。"""
    instant = {**HEALTHY_COLUMN, "spider_stats": {"response_count": SPIDER_RESPONSES, "avg_latency_ms": 0.0}}
    batch = [worker(1, "mn-a", instant), worker(2, "mn-b", instant)]

    stats = await _aggregate(monkeypatch, batch)

    assert stats.totalResponses == SPIDER_RESPONSES * WORKERS_IN_BATCH
    assert stats.avgLatencyMs == 0.0
