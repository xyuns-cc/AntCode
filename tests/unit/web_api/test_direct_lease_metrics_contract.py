"""Direct 控制面 Lease 的 metrics 契约必须覆盖 Worker 实际上报的全部字段。

真实环境事故：``DirectLeaseMetrics`` 是 ``extra="forbid"``，却比 Worker 的
``heartbeat_metrics_dict()`` 少了 9 个字段（proto ResourceMetrics 11-19 号）。
后果不是"少存点监控数据"，而是 Direct Worker 的**每一次心跳续租都被判 422**：
租约永不续期 → master 失租回收 → 容器无限重启，Worker 永远不会变 online。

首次签发走 ``metrics=None`` 所以能成功，问题只在续租暴露——这正是它能一路躲过
单测、直到真机 e2e 才现形的原因。用结构性断言把两侧字段集绑死。
"""

from types import SimpleNamespace

import pytest
from antcode_web_api.routes.v1.workers_direct_models import DirectLeaseMetrics
from antcode_worker.transport.gateway.heartbeat_metrics import heartbeat_metrics_dict

DEFAULT_MAX_CONCURRENT_TASKS = 5


def _full_metrics_heartbeat() -> SimpleNamespace:
    """构造一个把 heartbeat_metrics_dict 所有分支都走满的心跳。"""
    return SimpleNamespace(
        metrics=SimpleNamespace(
            cpu=12.5,
            memory=34.0,
            disk=56.0,
            running_tasks=2,
            max_concurrent_tasks=8,
            task_count=10,
            project_count=3,
            env_count=1,
            queued_tasks=4,
            cpu_cores=16,
            memory_total_bytes=68_719_476_736,
            memory_used_bytes=34_359_738_368,
            memory_available_bytes=34_359_738_368,
            disk_total_bytes=512_110_190_592,
            disk_used_bytes=256_055_095_296,
            disk_free_bytes=256_055_095_296,
            uptime_seconds=86_400,
            spider_stats=None,
        )
    )


def test_direct_lease_metrics_accepts_every_field_worker_actually_sends():
    payload = heartbeat_metrics_dict(_full_metrics_heartbeat())

    unknown = set(payload) - set(DirectLeaseMetrics.model_fields)
    assert not unknown, (
        f"Worker 上报了 DirectLeaseMetrics 不认识的字段 {sorted(unknown)}；"
        "extra='forbid' 会让每次心跳续租返回 422，Direct Worker 无法维持租约"
    )


def test_direct_lease_metrics_validates_the_real_payload():
    payload = heartbeat_metrics_dict(_full_metrics_heartbeat())

    model = DirectLeaseMetrics.model_validate(payload)

    assert model.queued_tasks == payload["queued_tasks"]
    assert model.uptime_seconds == payload["uptime_seconds"]
    assert model.memory_total_bytes == payload["memory_total_bytes"]


def test_metricsless_heartbeat_payload_also_validates():
    """metrics 缺省分支（只有 5 个键）同样必须能过校验。"""
    heartbeat = SimpleNamespace(
        metrics=None,
        cpu_percent=1.0,
        memory_percent=2.0,
        disk_percent=3.0,
        running_tasks=0,
        max_concurrent_tasks=DEFAULT_MAX_CONCURRENT_TASKS,
    )

    model = DirectLeaseMetrics.model_validate(heartbeat_metrics_dict(heartbeat))

    assert model.max_concurrent_tasks == DEFAULT_MAX_CONCURRENT_TASKS


@pytest.mark.parametrize(
    "proto_field",
    [
        "memory_total_bytes",
        "memory_used_bytes",
        "memory_available_bytes",
        "disk_total_bytes",
        "disk_used_bytes",
        "disk_free_bytes",
        "cpu_cores",
        "uptime_seconds",
        "queued_tasks",
    ],
)
def test_proto_resource_metrics_fields_are_representable(proto_field: str):
    """proto ResourceMetrics 11-19 号字段在 Direct 侧必须都有对应位置。"""
    assert proto_field in DirectLeaseMetrics.model_fields
