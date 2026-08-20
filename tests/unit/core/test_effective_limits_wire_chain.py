"""生效限额从 Worker 采集面走到资源页的整条链路，逐跳钉死。

这条链路上有六个**显式键表**（两条传输各一个 Hash 映射、共用的 metrics dict、
Gateway 解码、core 持久化白名单、读回 schema）。任何一处漏掉字段，链路上每一步
都仍然"成功"，只是数值在中途消失，页面永远显示"未知"——没有一处会报错，也没有
一条日志。逐跳断言是唯一能挡住这种静默丢失的方式。

真机取值：mn-worker-02 生效 537MB / 480s，而 DB 里的下发值是 1024MB。
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.worker_heartbeat_persistence import (
    build_redis_heartbeat_update,
)
from antcode_core.domain.schemas.worker import WorkerMetrics
from antcode_web_api.routes.v1.workers_resources import _effective_limits
from antcode_worker.heartbeat.reporter_models import Heartbeat, Metrics, OSInfo
from antcode_worker.transport.redis.heartbeat_hash import _heartbeat_mapping

EFFECTIVE_MEMORY_MB = 537
EFFECTIVE_CPU_SEC = 480


def _heartbeat() -> Heartbeat:
    return Heartbeat(
        worker_id="mn-worker-02",
        status="online",
        metrics=Metrics(
            max_concurrent_tasks=4,
            task_memory_limit_mb=EFFECTIVE_MEMORY_MB,
            task_cpu_time_limit_sec=EFFECTIVE_CPU_SEC,
        ),
        os_info=OSInfo(),
        timestamp=datetime.now(),
    )


def test_direct_hash_projection_carries_effective_limits() -> None:
    """Direct 传输把心跳投影成 Redis Hash，两项必须在映射表里。"""
    mapping = _heartbeat_mapping(_heartbeat())

    assert mapping["task_memory_limit_mb"] == str(EFFECTIVE_MEMORY_MB)
    assert mapping["task_cpu_time_limit_sec"] == str(EFFECTIVE_CPU_SEC)


class _CapturingPipeline:
    """只记录 hset 的 mapping，其余管线调用一律无副作用。"""

    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}

    def hset(self, _key: str, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def hdel(self, *_args: object) -> None:
        return None

    def expire(self, *_args: object) -> None:
        return None

    async def execute(self) -> None:
        return None


@pytest.mark.asyncio
async def test_gateway_hash_projection_carries_effective_limits() -> None:
    """Gateway 侧是**另一份**独立的 Hash 映射，键集必须与 Direct 侧一致。

    两条传输写同一个 heartbeat:{worker_id} 键；只补一侧会让"哪台机器显示得出
    限额"取决于它挂在哪种传输上。
    """
    from antcode_gateway.handlers.heartbeat import HeartbeatData, HeartbeatHandler

    pipeline = _CapturingPipeline()
    handler = HeartbeatHandler(redis_client=SimpleNamespace(pipeline=lambda **_kwargs: pipeline))

    assert await handler.handle(
        HeartbeatData(
            worker_id="mn-worker-03",
            task_memory_limit_mb=EFFECTIVE_MEMORY_MB,
            task_cpu_time_limit_sec=EFFECTIVE_CPU_SEC,
        )
    )

    assert pipeline.mapping["task_memory_limit_mb"] == str(EFFECTIVE_MEMORY_MB)
    assert pipeline.mapping["task_cpu_time_limit_sec"] == str(EFFECTIVE_CPU_SEC)


def test_persistence_allowlist_renames_to_camel_case() -> None:
    """core 的白名单同时负责 snake_case → camelCase 改名。"""
    mapping = _heartbeat_mapping(_heartbeat())
    update = build_redis_heartbeat_update(mapping, datetime.now())

    assert update.metrics["taskMemoryLimitMb"] == EFFECTIVE_MEMORY_MB
    assert update.metrics["taskCpuTimeLimitSec"] == EFFECTIVE_CPU_SEC


def test_readback_schema_accepts_persisted_metrics() -> None:
    """读回 schema 是 extra="forbid"：缺字段会让**整个** metrics 塌成默认值。

    塌陷是静默的（workers.py 的 except 分支），所以必须显式校验一次。
    """
    mapping = _heartbeat_mapping(_heartbeat())
    persisted = build_redis_heartbeat_update(mapping, datetime.now()).metrics

    model = WorkerMetrics(**persisted)

    assert model.taskMemoryLimitMb == EFFECTIVE_MEMORY_MB
    assert model.taskCpuTimeLimitSec == EFFECTIVE_CPU_SEC


def test_full_chain_reaches_the_resource_page() -> None:
    """采集面 → Hash → 持久化 → 资源页，端到端一次走通。"""
    mapping = _heartbeat_mapping(_heartbeat())
    persisted = build_redis_heartbeat_update(mapping, datetime.now()).metrics

    limits = _effective_limits(persisted)

    assert limits["task_memory_limit_mb"] == EFFECTIVE_MEMORY_MB
    assert limits["task_cpu_time_limit_sec"] == EFFECTIVE_CPU_SEC


def test_unreported_limits_stay_unknown_through_the_whole_chain() -> None:
    """控制组：Worker 没报（0）时，链路末端必须仍然是"不知道"。"""
    heartbeat = _heartbeat()
    heartbeat.metrics.task_memory_limit_mb = 0
    heartbeat.metrics.task_cpu_time_limit_sec = 0

    mapping = _heartbeat_mapping(heartbeat)
    persisted = build_redis_heartbeat_update(mapping, datetime.now()).metrics
    limits = _effective_limits(persisted)

    assert limits["task_memory_limit_mb"] is None
    assert limits["task_cpu_time_limit_sec"] is None
    assert json.dumps(limits)  # 可序列化：null 会原样出现在响应里
