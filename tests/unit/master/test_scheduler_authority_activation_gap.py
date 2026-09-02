"""任期 epoch 尚未落 PG ≠ 权威被更高任期顶掉：两种事实必须给出两种信号。

Leader 锁由最先轮询到的 loop 抢下，epoch 落库由 scheduler watcher 下一次 poll
才做；窗口内 reconcile 拿着合法的新 token 却读不到自己的 epoch。旧实现把它
报成 ``SchedulerAuthorityLost``，与"另一个 Master 抢走了权威"完全同形。
"""

import importlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from antcode_core.domain.models.scheduler_authority import (
    SCHEDULER_AUTHORITY_NAME,
    SchedulerAuthority,
)
from antcode_master.control.reconcile_loop import ReconcileLoop
from antcode_master.control.scheduler_authority import (
    SchedulerAuthorityLost,
    SchedulerAuthorityNotActivated,
    require_scheduler_authority,
)
from tortoise import Tortoise
from tortoise.transactions import in_transaction

loop_module = importlib.import_module("antcode_master.control.reconcile_loop")

TOKEN = 7


@pytest_asyncio.fixture
async def authority_db():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models.scheduler_authority"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def _activate(token: int) -> None:
    await SchedulerAuthority.create(
        name=SCHEDULER_AUTHORITY_NAME,
        fencing_token=token,
        activated_at=datetime.now(UTC),
    )


async def _require(token: int) -> None:
    async with in_transaction("default") as conn:
        await require_scheduler_authority(conn, token)


@pytest.mark.asyncio
async def test_missing_row_is_not_activated(authority_db) -> None:
    with pytest.raises(SchedulerAuthorityNotActivated, match="expected=7 current=None"):
        await _require(TOKEN)


@pytest.mark.asyncio
async def test_older_epoch_is_not_activated(authority_db) -> None:
    # fencing token 单调递增且只发一次，比我小的代际不可能是抢走我权威的人。
    await _activate(TOKEN - 1)

    with pytest.raises(SchedulerAuthorityNotActivated, match="expected=7 current=6"):
        await _require(TOKEN)


@pytest.mark.asyncio
async def test_newer_epoch_is_authority_lost(authority_db) -> None:
    await _activate(TOKEN + 1)

    with pytest.raises(SchedulerAuthorityLost, match="expected=7 current=8") as caught:
        await _require(TOKEN)
    assert not isinstance(caught.value, SchedulerAuthorityNotActivated)


@pytest.mark.asyncio
async def test_own_epoch_passes(authority_db) -> None:
    await _activate(TOKEN)

    await _require(TOKEN)


class _Recorder:
    """替掉 loguru 的 logger，区分"有 traceback"和"只有一行 info"。"""

    def __init__(self) -> None:
        self.exceptions: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def exception(self, message: str) -> None:
        self.exceptions.append(message)

    def info(self, message: str) -> None:
        self.infos.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def debug(self, message: str) -> None:
        pass


async def _run_one_tick(monkeypatch, failure: Exception) -> tuple[_Recorder, list[int]]:
    recorder = _Recorder()
    monkeypatch.setattr(loop_module, "logger", recorder)
    monkeypatch.setattr(loop_module, "ensure_leader", AsyncMock(return_value=True))
    monkeypatch.setattr(loop_module, "get_fencing_token", lambda: TOKEN)

    loop = ReconcileLoop(check_interval=0)
    loop._running = True
    seen: list[int] = []

    async def failing_reconcile(token: int) -> None:
        seen.append(token)
        loop._running = False
        raise failure

    monkeypatch.setattr(loop, "_reconcile", failing_reconcile)
    await loop._run_loop()
    return recorder, seen


@pytest.mark.asyncio
async def test_pending_epoch_skips_round_without_traceback(monkeypatch) -> None:
    recorder, seen = await _run_one_tick(
        monkeypatch,
        SchedulerAuthorityNotActivated("scheduler epoch 尚未激活: expected=7 current=None"),
    )

    assert seen == [TOKEN]
    assert recorder.exceptions == []
    assert [message for message in recorder.infos if "epoch 尚未落库" in message]


@pytest.mark.asyncio
async def test_real_authority_loss_still_reports_traceback(monkeypatch) -> None:
    recorder, seen = await _run_one_tick(
        monkeypatch,
        SchedulerAuthorityLost("scheduler authority changed: expected=7 current=8"),
    )

    assert seen == [TOKEN]
    assert recorder.exceptions == ["协调循环异常"]
    assert [message for message in recorder.infos if "epoch 尚未落库" in message] == []
