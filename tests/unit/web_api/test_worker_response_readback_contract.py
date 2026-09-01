"""`GET /workers` 读回 Worker 自报数据的键集契约。

`WorkerMetrics` / `WorkerCapabilities` 都是 ``extra="forbid"``，而写入它们所校验的
两列（``workers.metrics`` / ``workers.capabilities``）的是 **Worker 二进制**——另一个
容器、另一条发布线。键集错配时旧代码吞掉 ValidationError 退回默认值，于是整份
metrics 塌成 cpu=0 / maxConcurrentTasks=5，与一台真正空闲的 Worker 逐字节相同：
页面、日志、告警都分辨不出来。真机 mn 栈上三台 Worker 就这样报了一个版本的 0%。

所以这里钉两件事：
1. 生产者实际吐出的每一个键，读回 schema 都必须声明（键集契约，挡住下一次新增字段）；
2. 出现未声明的键时必须抛，不许再退回默认值（挡住"错配变静默"这个失败模式本身）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.worker_heartbeat_persistence import (
    build_redis_heartbeat_update,
    build_worker_heartbeat_update,
)
from antcode_core.domain.schemas.worker import WorkerCapabilities, WorkerMetrics
from antcode_web_api.routes.v1.workers import _worker_to_response
from antcode_worker.app.capabilities import detect_plugin_capabilities
from antcode_worker.heartbeat.reporter_models import Heartbeat, Metrics, OSInfo, SpiderStats
from antcode_worker.transport.redis.heartbeat_hash import _heartbeat_mapping

# 真机 mn-worker-02 的实测值：并发 4（不是 schema 默认的 5）、CPU 26.3%。
REPORTED_MAX_CONCURRENT = 4
REPORTED_CPU_PERCENT = 26.3
REPORTED_MEMORY_LIMIT_MB = 537
REPORTED_REQUEST_COUNT = 7
CONTROL_MAX_CONCURRENT = 3
CONTROL_CPU_PERCENT = 11.5


def _spider_heartbeat() -> Heartbeat:
    return Heartbeat(
        worker_id="mn-worker-02",
        status="online",
        metrics=Metrics(
            cpu=REPORTED_CPU_PERCENT,
            max_concurrent_tasks=REPORTED_MAX_CONCURRENT,
            task_memory_limit_mb=REPORTED_MEMORY_LIMIT_MB,
            spider_stats=SpiderStats(
                request_count=REPORTED_REQUEST_COUNT,
                status_codes={"200": REPORTED_REQUEST_COUNT},
                domain_stats=[{"domain": "example.com", "reqs": REPORTED_REQUEST_COUNT}],
            ),
        ),
        os_info=OSInfo(),
        timestamp=datetime.now(),
    )


def _persisted_metrics(heartbeat: Heartbeat) -> dict:
    """走真实的采集 → Hash 投影 → 持久化白名单，拿到落库时的 metrics 字典。"""
    return build_redis_heartbeat_update(_heartbeat_mapping(heartbeat), datetime.now()).metrics


def _worker(**overrides) -> SimpleNamespace:
    values = {
        "public_id": "worker-1",
        "name": "Worker 1",
        "host": "127.0.0.1",
        "port": 8001,
        "status": "online",
        "region": "",
        "description": "",
        "tags": [],
        "version": "",
        "metrics": None,
        "capabilities": None,
        "last_heartbeat": None,
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_persisted_metrics_keys_are_all_declared_by_readback_schema() -> None:
    """心跳落库的每一个 metrics 键，读回 schema 都得认识。

    这是键集契约本身：下一次给 Metrics 加字段时，忘了同步 schema 会在这里红，
    而不是等到真机上"页面全是 0"。
    """
    persisted = _persisted_metrics(_spider_heartbeat())

    assert set(persisted) <= set(WorkerMetrics.model_fields)


def test_persisted_heartbeat_metrics_keys_are_all_declared_by_readback_schema() -> None:
    """另一条落库路径同样要钉。

    ``build_worker_heartbeat_update`` 与 ``_redis_metrics`` 是同一个形状：先过
    ``extra="forbid"`` 的模型，再往 dict 里补塞 ``spider_stats``。补塞的键绕开了模型，
    上一条契约测试只覆盖了 Redis 那条投影；下次只在这条加字段照样漏到真机。
    """
    update = build_worker_heartbeat_update(
        heartbeat_at=datetime.now(),
        status="online",
        metrics=WorkerMetrics(maxConcurrentTasks=REPORTED_MAX_CONCURRENT).model_dump(),
        spider_stats={"request_count": REPORTED_REQUEST_COUNT},
        system_info={},
        capabilities=None,
    )

    assert set(update.metrics) <= set(WorkerMetrics.model_fields)


def test_reported_capability_keys_are_all_declared_by_readback_schema() -> None:
    """Worker 能力快照的生产者是 detect_plugin_capabilities，直接拿它的真实输出比。"""
    produced = detect_plugin_capabilities(SimpleNamespace(capabilities=lambda: ["code", "spider"]))

    assert set(produced) <= set(WorkerCapabilities.model_fields)


def test_response_keeps_real_metrics_for_worker_that_reported_spider_stats() -> None:
    """报过爬虫统计的 Worker：并发必须是上报的 4，不是 schema 默认的 5。"""
    persisted = _persisted_metrics(_spider_heartbeat())

    response = _worker_to_response(_worker(metrics=persisted))

    assert response.metrics.maxConcurrentTasks == REPORTED_MAX_CONCURRENT
    assert response.metrics.cpu == pytest.approx(REPORTED_CPU_PERCENT)
    assert response.metrics.taskMemoryLimitMb == REPORTED_MEMORY_LIMIT_MB
    assert response.metrics.spider_stats["request_count"] == REPORTED_REQUEST_COUNT


def test_response_keeps_real_capabilities_for_current_wire_contract_worker() -> None:
    """capabilities 缺 playwright / wire_contract 时，task_types 会一起塌成空表。"""
    produced = detect_plugin_capabilities(SimpleNamespace(capabilities=lambda: ["code", "spider"]))

    response = _worker_to_response(_worker(capabilities=produced))

    assert response.capabilities.task_types == ["code", "spider"]
    assert response.capabilities.curl_cffi == produced["curl_cffi"]
    assert response.capabilities.wire_contract == produced["wire_contract"]
    assert response.capabilities.playwright == produced["playwright"]


def test_unknown_metrics_key_never_collapses_to_defaults() -> None:
    """未声明的键绝不能塌成默认值：那等于用"一台空闲 Worker"冒充真实读数。

    置 None + 带键名的 snapshotErrors 是当前契约（爆炸半径见
    test_worker_list_row_isolation）；这里只钉"不许出现假读数"这条底线。
    """
    worker = _worker(metrics={"cpu": REPORTED_CPU_PERCENT, "unknownFutureField": 1})

    response = _worker_to_response(worker)

    assert response.metrics is None
    assert response.snapshotErrors[0].keys == ["unknownFutureField"]


def test_unknown_capability_key_never_collapses_to_defaults() -> None:
    worker = _worker(capabilities={"task_types": ["code"], "unknownFutureCapability": True})

    response = _worker_to_response(worker)

    assert response.capabilities is None
    assert response.snapshotErrors[0].keys == ["unknownFutureCapability"]


def test_worker_that_never_reported_spider_stats_still_shows_real_values() -> None:
    """控制组：修复前后都应通过。

    验证对象选错时（比如挑了一台恰好没报爬虫统计的机器）上面几条会一起变绿，
    这条在两侧都绿，用来确认差异确实来自未声明的键而非环境。
    """
    legacy_shaped = {
        "cpu": CONTROL_CPU_PERCENT,
        "maxConcurrentTasks": CONTROL_MAX_CONCURRENT,
        "taskMemoryLimitMb": REPORTED_MEMORY_LIMIT_MB,
    }

    response = _worker_to_response(
        _worker(metrics=legacy_shaped, capabilities={"curl_cffi": {"enabled": True}, "task_types": ["code"]})
    )

    assert response.metrics.maxConcurrentTasks == CONTROL_MAX_CONCURRENT
    assert response.metrics.cpu == pytest.approx(CONTROL_CPU_PERCENT)
    assert response.capabilities.task_types == ["code"]
