"""派发侧那几组用例共用的装配件（Worker 行 / Redis 心跳 / 日志采集）。

单独一个模块而不是塞进用例文件：``test_dispatch_survives_broken_metrics_column`` 与
``test_dispatch_metrics_unavailable`` 断言的是两个不同的缺陷，接的却是同一套桩，抄两份
迟早会漂。命名不带 ``test_`` 前缀，pytest 不会收集它（``dispatch_epoch_support`` 是同一个
惯例）。注入 Redis 读失败只有后者需要，那个假 Redis 留在它自己文件里。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import antcode_core.application.services.workers.worker_load_balancing as balancer_module
import antcode_core.infrastructure.redis as redis_module
from antcode_core.domain.models.enums import WorkerStatus
from loguru import logger

READY_FILTER = "antcode_core.application.services.workers.worker_load_balancing.filter_registration_ready_workers"

MAX_TASKS = 10
IDLE_CPU = 15
IDLE_MEMORY = 20
BUSY_CPU = 95
DISK = 10
COLUMN_ALARM = "不是 JSON 对象"

# 三种真实见过的坏形状。前两种从前抛 ValueError，第三种从前被静默拆成键值对。
LIST_COLUMN: list = [{"cpu": 1}]
STR_COLUMN = '{"cpu": 1}'
PAIRABLE_COLUMN = ["cp", "me"]

_TASK_TYPES = ["code", "rule", "spider"]


def worker(worker_id: int, name: str, metrics):
    return SimpleNamespace(
        id=worker_id,
        public_id=f"worker-{worker_id}",
        name=name,
        host="10.0.0.1",
        port=8000,
        region=None,
        status=WorkerStatus.ONLINE,
        transport_mode="direct",
        capabilities={"task_types": list(_TASK_TYPES)},
        metrics=metrics,
        resource_limits=None,
        last_heartbeat=None,
        tags=None,
    )


class _FakeRedis:
    def __init__(self, hashes: dict[str, dict[str, str]]):
        self.hashes = hashes

    async def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})


def heartbeat(cpu: int) -> dict[str, str]:
    return {
        "cpu": str(cpu),
        "memory": str(IDLE_MEMORY),
        "disk": str(DISK),
        "running_tasks": "0",
        "max_concurrent_tasks": str(MAX_TASKS),
        "queued_tasks": "0",
    }


def _heartbeat_key(node) -> str:
    return f"{{antcode}}:heartbeat:{node.public_id}"


def install_workers(monkeypatch, workers, *, cpu_by_name=None):
    """把这批 Worker 同时接到注册门禁、Worker 表和 Redis 心跳三处。"""
    cpu_by_name = cpu_by_name or {}
    hashes = {_heartbeat_key(node): heartbeat(cpu_by_name.get(node.name, IDLE_CPU)) for node in workers}
    monkeypatch.setattr(redis_module, "get_redis_client", AsyncMock(return_value=_FakeRedis(hashes)))
    monkeypatch.setattr(READY_FILTER, AsyncMock(return_value=list(workers)))

    class _Query:
        def filter(self, **_kwargs):
            return self

        async def all(self):
            return list(workers)

    monkeypatch.setattr(balancer_module, "Worker", SimpleNamespace(filter=lambda **_kwargs: _Query()))


class Records:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def text(self, level: str | None = None) -> str:
        return "\n".join(msg for lvl, msg in self.rows if level is None or lvl == level)


def capture_logs() -> tuple[Records, int]:
    records = Records()
    sink_id = logger.add(
        lambda message: records.rows.append((message.record["level"].name, message.record["message"])),
        level="WARNING",
    )
    return records, sink_id
