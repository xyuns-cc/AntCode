from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from antcode_core.application.services.lease_service import LeaseStore
from antcode_core.application.services.workers import run_settlement_guard as guard
from antcode_core.application.services.workers.run_ownership_fence import (
    ownership_token,
    run_owner_key,
)

EXPECTED_KEY_COUNT = 2
TEST_BATCH_SIZE = 2
EXPECTED_MGET_CALLS = 2
EXPECTED_ORPHAN_RUNS = 3


class FakeOwnershipRedis:
    """按 owner key 真实索引的 ownership 存储（只实现 guard 用到的 mget）。

    任何可能删除 ownership 的调用都被记录下来，用于证明"放行死代际"没有
    顺手拆掉围栏——guard 只允许读，不允许写。
    """

    def __init__(self, owners: dict[str, str]) -> None:
        self._owners = dict(owners)
        self.writes: list[str] = []

    async def mget(self, keys: list[str]) -> list[Any]:
        return [self._owners.get(key) for key in keys]

    async def delete(self, *keys: str) -> int:
        self.writes.append("delete")
        return 0

    async def unlink(self, *keys: str) -> int:
        self.writes.append("unlink")
        return 0

    async def eval(self, *args: Any) -> int:
        self.writes.append("eval")
        return 0


class RecordingProbe:
    """代际活性判据的替身（生产实现是 ``LeaseStore.is_current``）。"""

    def __init__(self, live_generations: set[tuple[str, str]]) -> None:
        self._live = set(live_generations)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, worker_id: str, lease_id: str) -> bool:
        self.calls.append((worker_id, lease_id))
        return (worker_id, lease_id) in self._live


def owner_map(bindings: dict[str, tuple[str, str]]) -> dict[str, str]:
    return {
        run_owner_key(run_id): ownership_token(worker_id, lease_id)
        for run_id, (worker_id, lease_id) in bindings.items()
    }


@pytest.mark.asyncio
async def test_empty_run_set_does_not_connect_to_redis(monkeypatch) -> None:
    get_redis = AsyncMock()
    monkeypatch.setattr(guard, "get_redis_client", get_redis)

    await guard.ensure_runs_settled([])

    get_redis.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_generation_owner_rejects_delete_and_names_the_holder() -> None:
    redis = FakeOwnershipRedis(owner_map({"run-2": ("worker-1", "lease-1")}))
    probe = RecordingProbe({("worker-1", "lease-1")})

    with pytest.raises(guard.RunSettlementPendingError) as exc_info:
        await guard.ensure_runs_settled(
            ["run-1", "run-2"],
            redis_client=redis,
            generation_probe=probe,
        )

    detail = str(exc_info.value)
    assert "run-2" in detail
    assert "worker-1" in detail
    assert probe.calls == [("worker-1", "lease-1")]


@pytest.mark.asyncio
async def test_dead_generation_owner_no_longer_blocks_delete() -> None:
    """SIGKILL 后的残留 ownership 不再把删除挡满 ~65 分钟的 TTL。"""
    redis = FakeOwnershipRedis(owner_map({"run-1": ("worker-1", "lease-dead")}))
    probe = RecordingProbe({("worker-1", "lease-new")})

    await guard.ensure_runs_settled(["run-1"], redis_client=redis, generation_probe=probe)

    assert probe.calls == [("worker-1", "lease-dead")]


@pytest.mark.asyncio
async def test_dead_generation_owner_key_is_never_released() -> None:
    """放行不等于拆围栏：guard 只读 ownership，绝不删除它。"""
    redis = FakeOwnershipRedis(owner_map({"run-1": ("worker-1", "lease-dead")}))

    await guard.ensure_runs_settled(
        ["run-1"],
        redis_client=redis,
        generation_probe=RecordingProbe(set()),
    )

    assert redis.writes == []


@pytest.mark.asyncio
async def test_liveness_probe_failure_fails_closed_as_unavailable() -> None:
    """活性证据不可得时既不放行也不谎称在结算，直接暴露为不可用。"""
    redis = FakeOwnershipRedis(owner_map({"run-1": ("worker-1", "lease-1")}))

    async def exploding_probe(worker_id: str, lease_id: str) -> bool:
        raise RuntimeError("lease store down: redis://secret@host")

    with pytest.raises(guard.RunSettlementGuardUnavailable, match="状态服务不可用") as exc_info:
        await guard.ensure_runs_settled(
            ["run-1"],
            redis_client=redis,
            generation_probe=exploding_probe,
        )

    assert "secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_unparseable_owner_token_fails_closed() -> None:
    redis = FakeOwnershipRedis({run_owner_key("run-1"): "no-separator"})
    probe = RecordingProbe(set())

    with pytest.raises(guard.RunSettlementGuardUnavailable, match="状态服务不可用"):
        await guard.ensure_runs_settled(["run-1"], redis_client=redis, generation_probe=probe)

    assert probe.calls == []


@pytest.mark.asyncio
async def test_same_dead_generation_is_probed_once_for_all_its_runs() -> None:
    bindings = {f"run-{index}": ("worker-1", "lease-dead") for index in range(EXPECTED_ORPHAN_RUNS)}
    redis = FakeOwnershipRedis(owner_map(bindings))
    probe = RecordingProbe(set())

    await guard.ensure_runs_settled(list(bindings), redis_client=redis, generation_probe=probe)

    assert probe.calls == [("worker-1", "lease-dead")]


def test_default_probe_is_the_authoritative_lease_predicate() -> None:
    """判据必须是围栏 Lua 自称对齐的那一个，不允许另起一份更松的活性定义。"""
    probe = guard._lease_generation_probe(object())

    assert probe.__func__ is LeaseStore.is_current


@pytest.mark.asyncio
async def test_released_runs_allow_delete_and_deduplicate_ids() -> None:
    redis = AsyncMock()
    redis.mget.return_value = [None, None]

    await guard.ensure_runs_settled(["run-1", "run-1", "run-2"], redis_client=redis)

    keys = redis.mget.await_args.args[0]
    assert len(keys) == EXPECTED_KEY_COUNT
    assert keys[0].endswith(":run:owner:run-1")
    assert keys[1].endswith(":run:owner:run-2")


@pytest.mark.asyncio
async def test_redis_failure_is_exposed() -> None:
    redis = AsyncMock()
    redis.mget.side_effect = RuntimeError("redis unavailable")

    with pytest.raises(guard.RunSettlementGuardUnavailable, match="状态服务不可用"):
        await guard.ensure_runs_settled(["run-1"], redis_client=redis)


@pytest.mark.asyncio
async def test_large_run_set_is_checked_in_bounded_batches(monkeypatch) -> None:
    monkeypatch.setattr(guard, "OWNERSHIP_LOOKUP_BATCH_SIZE", TEST_BATCH_SIZE)
    redis = AsyncMock()
    redis.mget.side_effect = [[None, None], [None]]

    await guard.ensure_runs_settled(["run-1", "run-2", "run-3"], redis_client=redis)

    assert redis.mget.await_count == EXPECTED_MGET_CALLS


@pytest.mark.asyncio
async def test_redis_client_failure_is_wrapped(monkeypatch) -> None:
    monkeypatch.setattr(
        guard,
        "get_redis_client",
        AsyncMock(side_effect=RuntimeError("connection contains sensitive context")),
    )

    with pytest.raises(guard.RunSettlementGuardUnavailable, match="状态服务不可用") as exc_info:
        await guard.ensure_runs_settled(["run-1"])

    assert "sensitive" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_mget_response_is_unavailable() -> None:
    redis = AsyncMock()
    redis.mget.return_value = []

    with pytest.raises(guard.RunSettlementGuardUnavailable, match="状态服务不可用"):
        await guard.ensure_runs_settled(["run-1"], redis_client=redis)


def test_only_explicit_terminal_statuses_are_deletable() -> None:
    from antcode_core.domain.models.enums import TaskStatus

    assert guard.TASK_RUN_TERMINAL_STATUSES == {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
        TaskStatus.SKIPPED,
        TaskStatus.REJECTED,
    }
    assert TaskStatus.PAUSED not in guard.TASK_RUN_TERMINAL_STATUSES


@pytest.mark.asyncio
async def test_load_deletable_runs_rejects_nonterminal_status(monkeypatch) -> None:
    from antcode_core.domain.models.task_run import TaskRun

    query = MagicMock()
    query.exists = AsyncMock(return_value=True)
    query.using_db.return_value = query
    query.exclude.return_value = query
    monkeypatch.setattr(TaskRun, "filter", lambda **_kwargs: query)
    settled = AsyncMock()
    monkeypatch.setattr(guard, "ensure_runs_settled", settled)

    with pytest.raises(ValueError, match="未终态"):
        await guard.load_deletable_run_ids(object(), [1])

    settled.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_deletable_runs_checks_ownership(monkeypatch) -> None:
    from antcode_core.domain.models.task_run import TaskRun

    query = MagicMock()
    query.exists = AsyncMock(return_value=False)
    query.all = AsyncMock(return_value=[SimpleNamespace(run_id="run-1")])
    query.using_db.return_value = query
    query.exclude.return_value = query
    query.only.return_value = query
    monkeypatch.setattr(TaskRun, "filter", lambda **_kwargs: query)
    settled = AsyncMock()
    monkeypatch.setattr(guard, "ensure_runs_settled", settled)

    run_ids = await guard.load_deletable_run_ids(object(), [1])

    assert run_ids == ["run-1"]
    settled.assert_awaited_once_with(["run-1"])


def test_task_delete_checks_settlement_before_dependency_delete() -> None:
    scheduler = Path(
        "packages/antcode_core/src/antcode_core/application/services/scheduler/scheduler_service.py"
    ).read_text(encoding="utf-8")

    assert scheduler.index("load_deletable_run_ids(conn, [task.id])") < scheduler.index(
        "delete_run_dependency_rows(conn, run_ids)"
    )


def test_project_delete_checks_settlement_before_run_delete() -> None:
    cascade = Path(
        "packages/antcode_core/src/antcode_core/application/services/projects/project_cascade_delete.py"
    ).read_text(encoding="utf-8")

    assert cascade.index("ensure_runs_settled(cleanup_run_ids)") < cascade.index(
        "TaskRun.filter(task_id__in=list(task_ids)).using_db(conn).delete()"
    )
