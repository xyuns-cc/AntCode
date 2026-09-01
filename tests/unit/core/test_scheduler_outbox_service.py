from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from antcode_core.application.services.scheduler.outbox_claims import OUTBOX_REPUBLISH_SECONDS
from antcode_core.application.services.scheduler.outbox_service import (
    OUTBOX_CONSUME_MAX_ATTEMPTS,
    OutboxConsumeClaim,
    SchedulerOutboxService,
)


class _Conn:
    """P1-DB-03: claim/heartbeat 以数据库时钟为权威 —— 假连接返回 DB NOW()。"""

    async def execute_query(self, sql, params=None):
        assert "NOW()" in sql
        return 1, [{"now": datetime.now(UTC)}]


class _Transaction(AbstractAsyncContextManager):
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _ClaimQuery:
    def __init__(self, event):
        self.first = AsyncMock(return_value=event)
        self.update = AsyncMock(return_value=1)

    def using_db(self, _connection):
        return self

    def select_for_update(self):
        return self


class _PublishQuery:
    def __init__(self, event=None):
        self.first = AsyncMock(return_value=event)
        self.update = AsyncMock(return_value=1)

    def using_db(self, _connection):
        return self

    def select_for_update(self, **_kwargs):
        return self

    def order_by(self, *_fields):
        return self


@pytest.mark.asyncio
async def test_enqueue_uses_supplied_transaction_connection():
    service = SchedulerOutboxService(stream=MagicMock())
    connection = MagicMock()
    created = MagicMock()

    with patch(
        "antcode_core.application.services.scheduler.outbox_service.SchedulerOutbox.create",
        AsyncMock(return_value=created),
    ) as create:
        result = await service.enqueue(
            "task_changed",
            "task",
            42,
            {"task_id": "42"},
            connection=connection,
        )

    assert result is created
    assert create.await_args.kwargs["using_db"] is connection


@pytest.mark.asyncio
async def test_publish_adds_stable_outbox_id_without_maxlen():
    stream = MagicMock()
    stream.xadd = AsyncMock()
    service = SchedulerOutboxService(stream=stream)
    event = MagicMock()
    event.payload = {"task_id": "42"}
    event.event_type = "task_changed"
    event.public_id = "event-id"
    event.created_at = datetime.now(UTC)

    await service._publish(event)

    args = stream.xadd.await_args.args
    assert args[1]["outbox_id"] == "event-id"
    assert stream.xadd.await_args.kwargs == {}


@pytest.mark.asyncio
async def test_published_unconsumed_event_is_republished_after_recovery_window():
    now = datetime.now(UTC)
    event = MagicMock(
        id=7,
        payload={"task_id": "42"},
        event_type="task_changed",
        public_id="event-id",
        created_at=now - timedelta(minutes=5),
    )
    select_query = _PublishQuery(event)
    update_query = _PublishQuery()
    stream = MagicMock(xadd=AsyncMock())
    service = SchedulerOutboxService(stream=stream)

    with (
        patch(
            "antcode_core.application.services.scheduler.outbox_service.in_transaction",
            return_value=_Transaction(),
        ),
        patch.object(service, "_db_now", AsyncMock(return_value=now)),
        patch(
            "antcode_core.application.services.scheduler.outbox_service.SchedulerOutbox.filter",
            MagicMock(side_effect=[select_query, update_query]),
        ) as outbox_filter,
    ):
        assert await service._publish_one() is True

    replay_filter = outbox_filter.call_args_list[0]
    replay_q = replay_filter.args[0]
    assert replay_q.children[0].filters == {"published_at__isnull": True}
    assert replay_q.children[1].filters == {"published_at__lte": now - timedelta(seconds=OUTBOX_REPUBLISH_SECONDS)}
    assert replay_filter.kwargs["consumed_at__isnull"] is True
    stream.xadd.assert_awaited_once()
    assert update_query.update.await_args.kwargs["published_at"] == now


@pytest.mark.asyncio
async def test_stale_consumption_claim_can_be_taken_over():
    event = MagicMock(
        id=7,
        consumed_at=None,
        consume_owner="dead-master",
        consume_started_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    query = _ClaimQuery(event)
    service = SchedulerOutboxService(stream=MagicMock())

    with (
        patch(
            "antcode_core.application.services.scheduler.outbox_service.in_transaction",
            return_value=_Transaction(),
        ),
        patch(
            "antcode_core.application.services.scheduler.outbox_service.SchedulerOutbox.filter",
            MagicMock(return_value=query),
        ),
    ):
        claim = await service.claim_consumption("outbox-1", "new-master")

    assert claim == OutboxConsumeClaim.CLAIMED
    assert query.update.await_args.kwargs["consume_owner"] == "new-master"


@pytest.mark.asyncio
async def test_failed_consumption_is_made_publishable_again():
    event = MagicMock(id=7, attempts=0, consume_attempts=0, consume_owner=None, consume_started_at=None)
    query = _ClaimQuery(event)
    service = SchedulerOutboxService(stream=MagicMock())

    with (
        patch(
            "antcode_core.application.services.scheduler.outbox_service.in_transaction",
            return_value=_Transaction(),
        ),
        patch(
            "antcode_core.application.services.scheduler.outbox_service.SchedulerOutbox.filter",
            MagicMock(return_value=query),
        ),
    ):
        requeued = await service.requeue_consumption_failure("outbox-1", "cleanup unavailable")

    assert requeued is True
    update = query.update.await_args.kwargs
    assert update["published_at"] is None
    assert update["consume_owner"] is None
    assert update["last_error"] == "cleanup unavailable"
    assert update["consume_attempts"] == 1
    assert "attempts" not in update


@pytest.mark.asyncio
async def test_repeated_consumption_failure_gives_up_terminally():
    """L3: 消费重投达上限后终止(标记 consumed),不再 republish,阻断 poison 循环。"""
    # P2 §4.5: 消费重投计数独立于 publish attempts —— publish 曾退避多次
    # (attempts 偏高) 不影响消费侧配额。
    event = MagicMock(
        id=7,
        attempts=99,
        consume_attempts=OUTBOX_CONSUME_MAX_ATTEMPTS - 1,
        consume_owner=None,
        consume_started_at=None,
    )
    query = _ClaimQuery(event)
    service = SchedulerOutboxService(stream=MagicMock())

    with (
        patch(
            "antcode_core.application.services.scheduler.outbox_service.in_transaction",
            return_value=_Transaction(),
        ),
        patch(
            "antcode_core.application.services.scheduler.outbox_service.SchedulerOutbox.filter",
            MagicMock(return_value=query),
        ),
    ):
        requeued = await service.requeue_consumption_failure("outbox-1", "poison event")

    assert requeued is False
    update = query.update.await_args.kwargs
    assert "published_at" not in update  # 终止:不再让事件重新可发布
    assert update["consumed_at"] is not None
    assert update["consume_attempts"] == OUTBOX_CONSUME_MAX_ATTEMPTS
    assert "attempts" not in update
    # P1-round6 5.2: 终止性 last_error 必须带 TERMINATED_PREFIX,
    # 让运维查询 last_error__startswith 精确圈出重试耗尽事件。
    from antcode_core.application.services.scheduler.outbox_service import OUTBOX_TERMINATED_PREFIX

    assert update["last_error"].startswith(OUTBOX_TERMINATED_PREFIX)
    assert "poison event" in update["last_error"]


@pytest.mark.asyncio
async def test_requeue_skips_event_actively_claimed_by_another_consumer():
    """P1-DB-01: 事件已被其他消费者活跃接管时，requeue 不得清掉其 claim。"""
    event = MagicMock(
        id=7,
        attempts=0,
        consume_attempts=0,
        consume_owner="master-b",
        consume_started_at=datetime.now(UTC) + timedelta(seconds=5),
    )
    query = _ClaimQuery(event)
    service = SchedulerOutboxService(stream=MagicMock())

    with (
        patch(
            "antcode_core.application.services.scheduler.outbox_service.in_transaction",
            return_value=_Transaction(),
        ),
        patch(
            "antcode_core.application.services.scheduler.outbox_service.SchedulerOutbox.filter",
            MagicMock(return_value=query),
        ),
    ):
        requeued = await service.requeue_consumption_failure("outbox-1", "stale loser retry")

    assert requeued is True
    query.update.assert_not_awaited()
