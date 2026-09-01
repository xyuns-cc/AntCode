"""``workers.metrics`` 坏成非 JSON 对象之后，心跳落库必须把它盖掉，而不是从此写不进去。

0ee5631 把读取侧（``probe_worker_resources`` / ``/workers/load/ranking``）与派发侧收敛到了
``persisted_worker_metrics`` 这一个契约上：坏列是**这一台**的数据问题，降级成"没有落库指标"、
点名打一条 WARNING，机器照常参与派发。写入侧当时没动，于是这一列上留下了两种互相矛盾的口径：

- 读：坏列可以带着继续跑；
- 写：``{**(worker.metrics or {}), **update.metrics}`` 对任何非 Mapping 一律抛 ``TypeError``。

这不是"多一次报错"而是死结——``_apply_update`` 是全仓唯一会重写这一列的写入方（另外两处
``worker.metrics =`` 在 410 掉的旧注册路径上），它一抛，整个事务回滚，坏值再也没人覆盖得掉，
那台 Worker 的 ``last_heartbeat`` 也从此不再前进。真机上这条异常落在
``smart_health_check`` 的 ``gather(return_exceptions=True)`` 里，运维只看得到一句
``节点检测异常: 'list' object is not a mapping``：没有机器名、没有列名、没有 worker_id。

顺带纠正一处流传的说法：``["cp", "me"]`` 被静默拆成 ``{"c": "p", "m": "e"}`` 是 ``dict.update``
的行为；``{**x}`` 走的是 ``keys()`` 协议，对 list/str **一律**抛 TypeError。所以写入侧三种坏
形状都是响亮的，这里不存在"静默污染"那一支。

**证伪方式**：把 ``_apply_update`` 里的 ``persisted_worker_metrics(worker)`` 换回
``worker.metrics``，本文件前两组（4 条）全部变红（``TypeError: 'list'/'str' object is not a
mapping``）。第三组是非证伪项，见其 docstring。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import pytest_asyncio
from antcode_core.application.services.workers.worker_heartbeat_persistence import (
    WorkerHeartbeatUpdate,
    persist_worker_heartbeat,
)
from antcode_core.domain.models import Worker, WorkerStatus
from loguru import logger
from tortoise import Tortoise

from tests.unit.core.broken_metrics_column_support import (
    COLUMN_ALARM,
    LIST_COLUMN,
    PAIRABLE_COLUMN,
    STR_COLUMN,
    capture_logs,
)

BEAT_CPU = 2.0
NEXT_BEAT_CPU = 3.0
PERSISTED_TASK_COUNT = 7

# 字符串标量要自己再 json 编码一层：Tortoise 的 JSONField 把 str 当成"已经是 JSON 文本"原样
# 下发（tortoise/fields/data.py::to_db_value），直接塞 '{"cpu": 1}' 进去存下的是个**对象**，
# 复现不出这个形状。真机上用 ``UPDATE ... = $1::jsonb`` planted 的是同一个值。
BROKEN_COLUMNS = [
    pytest.param(LIST_COLUMN, id="list-of-dict"),
    pytest.param(PAIRABLE_COLUMN, id="pairable-list"),
    pytest.param(json.dumps(STR_COLUMN), id="double-encoded-str"),
]


@pytest_asyncio.fixture(autouse=True)
async def database(tmp_path):
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'heartbeat-metrics.sqlite3'}",
        modules={
            "models": [
                "antcode_core.domain.models.worker",
                "antcode_core.domain.models.worker_install_key",
            ]
        },
        use_tz=True,
        timezone="UTC",
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()
    await Tortoise._reset_apps()


def beat(metrics: dict) -> WorkerHeartbeatUpdate:
    return WorkerHeartbeatUpdate(
        heartbeat_at=datetime.now().astimezone(),
        status=WorkerStatus.ONLINE.value,
        metrics=metrics,
        system_info={},
        capabilities=None,
    )


async def worker_with_column(name: str, column) -> Worker:
    """先建行再单独写列：``metrics`` 要绕开构造器，才能存成模型注解不允许的形状。"""
    worker = await Worker.create(name=name, host="127.0.0.1", status=WorkerStatus.ONLINE.value)
    await Worker.filter(id=worker.id).update(metrics=column)
    return await Worker.get(id=worker.id)


# --------------------------------------------------------------------------------------
# 1. 坏列会被下一拍心跳盖掉，且点名到机器和实际类型
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("column", BROKEN_COLUMNS)
@pytest.mark.asyncio
async def test_the_next_heartbeat_overwrites_a_broken_metrics_column(column) -> None:
    """坏列不是死结：这一拍照常写下一个干净的 JSON 对象，列自己就好了。"""
    broken = await worker_with_column("mn-broken", column)
    assert not isinstance(broken.metrics, dict)

    records, sink_id = capture_logs()
    try:
        persisted = await persist_worker_heartbeat(broken.id, beat({"cpu": BEAT_CPU}))
    finally:
        logger.remove(sink_id)

    assert persisted is not None
    reloaded = await Worker.get(id=broken.id)
    assert reloaded.metrics == {"cpu": BEAT_CPU}
    assert reloaded.last_heartbeat is not None

    warnings = records.text("WARNING")
    assert COLUMN_ALARM in warnings
    assert "mn-broken" in warnings
    assert type(column).__name__ in warnings


@pytest.mark.asyncio
async def test_a_healed_column_goes_back_to_incremental_merging() -> None:
    """自愈的反面判据：盖掉坏列**不等于**从此改成整列替换。

    第二拍只带 cpu，上一拍写进去的 taskCount 必须还在；同时不许再冒出"列坏了"的告警——
    坏列已经不在库里了，还在报就说明修的是读取而不是写入。
    """
    broken = await worker_with_column("mn-healed", PAIRABLE_COLUMN)

    await persist_worker_heartbeat(broken.id, beat({"cpu": BEAT_CPU, "taskCount": PERSISTED_TASK_COUNT}))
    records, sink_id = capture_logs()
    try:
        await persist_worker_heartbeat(broken.id, beat({"cpu": NEXT_BEAT_CPU}))
    finally:
        logger.remove(sink_id)

    reloaded = await Worker.get(id=broken.id)
    assert reloaded.metrics == {"cpu": NEXT_BEAT_CPU, "taskCount": PERSISTED_TASK_COUNT}
    assert COLUMN_ALARM not in records.text()


# --------------------------------------------------------------------------------------
# 非证伪项
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_healthy_column_keeps_its_own_keys_and_raises_no_alarm() -> None:
    """**非证伪项**：修复前后都绿（控制组）。

    它不刻画修复，只钉住"这一行换成契约检查之后，正常列的合并语义一个字没变"：落库列独有
    的 taskCount 照常保留，也不许因为多了一次契约检查就凭空多出告警。
    """
    healthy = await worker_with_column("mn-healthy", {"cpu": 1.0, "taskCount": PERSISTED_TASK_COUNT})

    records, sink_id = capture_logs()
    try:
        persisted = await persist_worker_heartbeat(healthy.id, beat({"cpu": BEAT_CPU}))
    finally:
        logger.remove(sink_id)

    assert persisted is not None
    reloaded = await Worker.get(id=healthy.id)
    assert reloaded.metrics == {"cpu": BEAT_CPU, "taskCount": PERSISTED_TASK_COUNT}
    assert records.text() == ""
