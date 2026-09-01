"""``workers.metrics`` 不是对象时，`GET /workers` 不许整页 500；列为空时不许编造读数。

两条都走 ``_read_column``，都在 25d4c34 / f3864ee 之后仍然活着：

**结构坏了 → 整页 500。** ``_read_column`` 只 ``except ValidationError``，可 ``model(**raw)``
在 ``raw`` 是 list 或被二次编码成字符串时抛的是 **TypeError**，pydantic 不接管。
真机 mn 栈实测（本次修复前）：往 ``workers.metrics`` 写 ``[{"cpu": 1}]``，
``GET /workers`` 返回 500，body 只有 ``{"success":false,"code":500,"message":"服务器内部错误"}``，
web-api 的 traceback 停在 ``worker_snapshot_readback.py:72``，里面既没有 worker_id 也没有列名。
``jsonb`` 存得下数组和字符串，所以这不是假想的形状。这与 eb7199a
（``for rule in v`` 抛 TypeError 而 pydantic 只接管 ValueError）是同一种错配。

**空列 → 编造出一台空闲机器。** ``if not raw: return model()`` 把一台从没上报过的
Worker 渲染成 cpu 0 / 并发 5 —— 而 ``worker_snapshot`` 的模块注释自己就写着"塌成默认值
与一台真正空闲的 Worker 逐字节相同"不可接受，前端 ``WorkerMetricCell`` 也早就按
"metrics=null 且 snapshotErrors 为空 = 还没心跳"渲染 '—'。也就是说后端从来没产出过
前端那条分支，那台机器在页面上一直显示 0.0%。

**证伪方式**：删掉 ``_read_column`` 的 ``isinstance(raw, Mapping)`` 分支，
``test_metrics_stored_as_*`` / ``test_structural_*`` 变红（TypeError 冒到路由外）；
把 ``if not raw: return None, None`` 改回 ``return model(), None``，
``test_never_reported_*`` 变红。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from antcode_core.domain.schemas.worker_snapshot import SnapshotErrorReason
from antcode_web_api.routes.v1.workers_crud import get_workers
from loguru import logger

CLEAN_CPU = 26.3
CLEAN_MAX_CONCURRENT = 4
UNKNOWN_KEY = "gpuUtilization"
PAGE = 1
PAGE_SIZE = 20
# 一台坏的 + 一台干净的（干净那台就是控制组）
EXPECTED_ROW_COUNT = 2

# 真机上构造过的两种坏形状：jsonb 数组、以及被二次编码成 JSON 字符串。
METRICS_AS_LIST = [{"cpu": 1.0}]
METRICS_AS_DOUBLE_ENCODED_STRING = '{"cpu": 1.5}'


def _worker(public_id: str, **overrides) -> SimpleNamespace:
    values = {
        "public_id": public_id,
        "name": public_id,
        "host": "127.0.0.1",
        "port": 8001,
        "status": "online",
        "region": "",
        "description": "",
        "tags": [],
        "version": "",
        "metrics": {"cpu": CLEAN_CPU, "maxConcurrentTasks": CLEAN_MAX_CONCURRENT},
        "capabilities": {"task_types": ["code"]},
        "last_heartbeat": None,
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _list_workers(workers: list[SimpleNamespace]):
    """走真实的列表 handler，而不是直接调 ``_read_column``：500 是在路由那一层发生的。"""
    from antcode_web_api.routes.v1.workers import _worker_to_response

    async def _list_accessible(_current_user, **_kwargs):
        return workers, len(workers)

    payload = await get_workers(
        page=PAGE,
        size=PAGE_SIZE,
        status_filter=None,
        region=None,
        search=None,
        current_user=SimpleNamespace(user_id="u1"),
        list_accessible_workers=_list_accessible,
        worker_to_response=_worker_to_response,
    )
    return {item.id: item for item in payload.data.items}


def _metrics_error(row):
    return next(error for error in row.snapshotErrors if error.column == "metrics")


# --------------------------------------------------------------------------------------
# 结构坏了：不许把整页打成 500
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "stored"),
    [("json-list", METRICS_AS_LIST), ("double-encoded-string", METRICS_AS_DOUBLE_ENCODED_STRING)],
)
@pytest.mark.asyncio
async def test_metrics_stored_as_a_non_object_does_not_take_down_the_whole_list(label, stored) -> None:
    """控制组是同一次请求里的两台干净 Worker：它们必须仍然返回真值。"""
    rows = await _list_workers([_worker("mn-worker-01", metrics=stored), _worker("mn-worker-02")])

    assert len(rows) == EXPECTED_ROW_COUNT, f"{label}: 坏行把整页带走了"
    clean = rows["mn-worker-02"]
    assert clean.metrics is not None and clean.metrics.cpu == pytest.approx(CLEAN_CPU)
    assert clean.snapshotErrors == []


@pytest.mark.parametrize(
    ("stored", "type_name"),
    [(METRICS_AS_LIST, "list"), (METRICS_AS_DOUBLE_ENCODED_STRING, "str")],
)
@pytest.mark.asyncio
async def test_non_object_column_reports_the_actual_stored_type(stored, type_name) -> None:
    """坏行既不能塌成默认值，也不能只留一个看不出原因的空位。"""
    rows = await _list_workers([_worker("mn-worker-01", metrics=stored)])
    error = _metrics_error(rows["mn-worker-01"])

    assert rows["mn-worker-01"].metrics is None
    assert error.reason is SnapshotErrorReason.NOT_AN_OBJECT
    assert type_name in error.message, "得说得出这一列实际存的是什么类型"


@pytest.mark.asyncio
async def test_structural_failure_is_machine_distinguishable_from_field_drift() -> None:
    """两种坏法的处置完全不同，不能只靠 message 里的中文区分。

    字段漂移 → 去补读回 schema，``keys`` 指得出是哪几个键；
    结构坏了 → 去查写这一列的那条路径，根本没有"哪个键"可指。
    """
    rows = await _list_workers(
        [
            _worker("mn-structural", metrics=METRICS_AS_LIST),
            _worker("mn-drift", metrics={"cpu": CLEAN_CPU, UNKNOWN_KEY: 42}),
        ]
    )
    structural = _metrics_error(rows["mn-structural"])
    drift = _metrics_error(rows["mn-drift"])

    assert structural.reason is SnapshotErrorReason.NOT_AN_OBJECT
    assert structural.keys == [], "结构坏了时没有键名可指，不许编一个出来"
    assert drift.reason is SnapshotErrorReason.FIELD_MISMATCH
    assert drift.keys == [UNKNOWN_KEY]


@pytest.mark.asyncio
async def test_non_object_column_names_the_worker_in_the_log() -> None:
    """响应体是给页面看的，日志是给运维看的；改之前 traceback 里两样都没有。"""
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING")
    try:
        await _list_workers([_worker("mn-worker-01", metrics=METRICS_AS_LIST), _worker("mn-worker-02")])
    finally:
        logger.remove(sink_id)

    warning = "".join(records)
    assert "mn-worker-01" in warning
    assert SnapshotErrorReason.NOT_AN_OBJECT.value in warning
    assert "mn-worker-02" not in warning, "干净的那台不该被牵连进告警"


# --------------------------------------------------------------------------------------
# 空列：不许编造出一台空闲机器
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("stored", [None, {}])
@pytest.mark.asyncio
async def test_never_reported_column_is_null_instead_of_a_fabricated_idle_worker(stored) -> None:
    """从没上报过的机器返回 null，不是 cpu=0 / 并发 5。

    ``maxConcurrentTasks`` 是这里最锋利的一刀：schema 默认 5，而真机上报的是 4 ——
    编出来的那份读数不只是"全 0"，它还会把并发容量说成一个没人配过的数。
    """
    rows = await _list_workers([_worker("mn-never-reported", metrics=stored, capabilities=stored)])
    row = rows["mn-never-reported"]

    assert row.metrics is None
    assert row.capabilities is None


@pytest.mark.asyncio
async def test_never_reported_is_distinguishable_from_a_read_failure() -> None:
    """前端按"null 且 snapshotErrors 为空 = 还没心跳"渲染 '—'，按"null 且有错"渲染红色。

    后端必须真的产出这两种，否则前端那条分支是死代码（改之前正是如此）。
    """
    rows = await _list_workers(
        [
            _worker("mn-never-reported", metrics=None),
            _worker("mn-unreadable", metrics=METRICS_AS_LIST),
        ]
    )

    assert rows["mn-never-reported"].metrics is None
    assert rows["mn-never-reported"].snapshotErrors == [], "还没心跳不是故障，不许伪装成读取失败"
    assert rows["mn-unreadable"].metrics is None
    assert rows["mn-unreadable"].snapshotErrors != [], "读取失败不许伪装成还没心跳"


@pytest.mark.asyncio
async def test_reported_worker_still_returns_its_real_values() -> None:
    """控制组：修复前后都必须绿。全绿说明差异来自这一列的形状，而不是这套桩。"""
    rows = await _list_workers([_worker("mn-worker-02"), _worker("mn-worker-03")])

    for row in rows.values():
        assert row.metrics is not None
        assert row.metrics.cpu == pytest.approx(CLEAN_CPU)
        assert row.metrics.maxConcurrentTasks == CLEAN_MAX_CONCURRENT
        assert row.capabilities is not None and row.capabilities.task_types == ["code"]
        assert row.snapshotErrors == []
