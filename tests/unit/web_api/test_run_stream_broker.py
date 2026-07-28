"""RunStreamBroker global capacity, queue bounds, and lifecycle tests."""

import asyncio

import pytest
from antcode_web_api.streams.run_stream_broker import (
    QUEUE_CAPACITY_UNAVAILABLE,
    QUEUE_OVERFLOW,
    RunStreamBroker,
    StreamLimitExceededError,
)

from tests.unit.web_api.fake_stream_capacity import FakeStreamCapacityLimiter, make_broker


@pytest.mark.asyncio
async def test_subscribe_unsubscribe_releases_empty_run_state():
    broker = make_broker()
    first = await broker.subscribe("run-1", user_id=7)
    second = await broker.subscribe("run-1", user_id=7)

    assert broker.has_subscribers("run-1") is True
    assert (await broker.stats())["total_subscriptions"] == 2
    await broker.unsubscribe(first)
    assert broker.has_subscribers("run-1") is True
    await broker.unsubscribe(second)

    assert broker.has_subscribers("run-1") is False
    assert "run-1" not in broker._subscriptions
    assert broker._user_counts == {}
    assert (await broker.stats())["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_unsubscribe_is_idempotent():
    broker = make_broker()
    subscription = await broker.subscribe("run-1", user_id=7)

    await broker.unsubscribe(subscription)
    await broker.unsubscribe(subscription)

    assert (await broker.stats())["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_per_run_limit_is_global_across_brokers():
    limiter = FakeStreamCapacityLimiter()
    first_broker = RunStreamBroker(limiter)
    second_broker = RunStreamBroker(limiter)
    first_broker.max_per_run = second_broker.max_per_run = 1
    first = await first_broker.subscribe("run-1", user_id=1)

    with pytest.raises(StreamLimitExceededError, match="执行记录"):
        await second_broker.subscribe("run-1", user_id=2)

    await first_broker.unsubscribe(first)


@pytest.mark.asyncio
async def test_per_user_limit_is_global_across_brokers():
    limiter = FakeStreamCapacityLimiter()
    first_broker = RunStreamBroker(limiter)
    second_broker = RunStreamBroker(limiter)
    first_broker.max_per_user = second_broker.max_per_user = 1
    first = await first_broker.subscribe("run-1", user_id=7)

    with pytest.raises(StreamLimitExceededError, match="当前用户"):
        await second_broker.subscribe("run-2", user_id=7)

    await first_broker.unsubscribe(first)


@pytest.mark.asyncio
async def test_total_limit_is_global_across_brokers():
    limiter = FakeStreamCapacityLimiter()
    first_broker = RunStreamBroker(limiter)
    second_broker = RunStreamBroker(limiter)
    first_broker.max_total = second_broker.max_total = 1
    first = await first_broker.subscribe("run-1", user_id=1)

    with pytest.raises(StreamLimitExceededError, match="服务端"):
        await second_broker.subscribe("run-2", user_id=2)

    await first_broker.unsubscribe(first)


@pytest.mark.asyncio
async def test_slow_consumer_receives_overflow_sentinel_and_stops_delivery():
    broker = make_broker()
    subscription = await broker.subscribe("run-1", user_id=7)
    subscription.queue = asyncio.Queue(maxsize=1)

    broker.publish("run-1", {"type": "log_line", "seq": 1})
    broker.publish("run-1", {"type": "log_line", "seq": 2})

    assert subscription.overflowed is True
    assert subscription.queue.get_nowait() is QUEUE_OVERFLOW
    assert (await broker.stats())["local"]["overflow_disconnects"] == 1
    broker.publish("run-1", {"type": "log_line", "seq": 3})
    assert subscription.queue.empty()
    await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_overflow_sentinel_is_first_element_not_behind_backlog():
    broker = make_broker()
    subscription = await broker.subscribe("run-1", user_id=7)
    subscription.queue = asyncio.Queue(maxsize=3)

    for seq in range(4):
        broker.publish("run-1", {"type": "log_line", "seq": seq})

    assert subscription.overflowed is True
    assert subscription.queue.get_nowait() is QUEUE_OVERFLOW
    assert subscription.queue.empty()
    await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_publish_without_subscribers_is_noop():
    broker = make_broker()
    broker.publish("run-x", {"type": "log_line"})

    assert (await broker.stats())["total_subscriptions"] == 0


@pytest.mark.asyncio
async def test_queue_enforces_utf8_byte_limit_before_message_count_limit():
    broker = make_broker()
    broker.max_queue_bytes = 32
    subscription = await broker.subscribe("run-1", user_id=7)

    broker.publish("run-1", {"type": "log_line", "data": {"content": "x" * 64}})

    assert subscription.overflowed is True
    assert subscription.pending_bytes == 0
    assert subscription.queue.get_nowait() is QUEUE_OVERFLOW
    await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_renewal_failure_explicitly_terminates_subscription():
    limiter = FakeStreamCapacityLimiter()
    limiter.renew_error = RuntimeError("redis unavailable")
    broker = RunStreamBroker(limiter)
    broker._renewal_interval = 0
    subscription = await broker.subscribe("run-1", user_id=7)

    message = await broker.get_message(subscription, timeout=1)

    assert message is QUEUE_CAPACITY_UNAVAILABLE
    await broker.unsubscribe(subscription)


@pytest.mark.asyncio
async def test_release_failure_is_exposed_after_local_state_is_cleaned():
    limiter = FakeStreamCapacityLimiter()
    broker = RunStreamBroker(limiter)
    subscription = await broker.subscribe("run-1", user_id=7)
    limiter.release_error = RuntimeError("redis unavailable")

    with pytest.raises(RuntimeError, match="redis unavailable"):
        await broker.unsubscribe(subscription)

    assert broker.has_subscribers("run-1") is False
    assert broker._total == 0


@pytest.mark.asyncio
async def test_stats_distinguishes_global_and_process_local_values():
    limiter = FakeStreamCapacityLimiter()
    first_broker = RunStreamBroker(limiter)
    second_broker = RunStreamBroker(limiter)
    first = await first_broker.subscribe("run-1", user_id=1)
    second = await second_broker.subscribe("run-2", user_id=2)

    stats = await first_broker.stats()

    assert stats["scope"] == "global"
    assert stats["total_subscriptions"] == 2
    assert stats["local"]["total_subscriptions"] == 1
    await first_broker.unsubscribe(first)
    await second_broker.unsubscribe(second)
