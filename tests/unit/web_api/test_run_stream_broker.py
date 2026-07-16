"""RunStreamBroker：订阅计数、上限、慢消费者溢出、内存卫生。"""

import asyncio

import pytest
from antcode_web_api.streams.run_stream_broker import (
    QUEUE_OVERFLOW,
    RunStreamBroker,
    StreamLimitExceededError,
)


def test_subscribe_unsubscribe_releases_empty_run_state():
    broker = RunStreamBroker()

    first = broker.subscribe("run-1", user_id=7)
    second = broker.subscribe("run-1", user_id=7)
    assert broker.has_subscribers("run-1") is True
    assert broker.stats()["total_subscriptions"] == 2

    broker.unsubscribe(first)
    assert broker.has_subscribers("run-1") is True

    broker.unsubscribe(second)
    # 对齐原 ConnectionPool 的内存卫生：空 run / 空 user 键不得残留
    assert broker.has_subscribers("run-1") is False
    assert "run-1" not in broker._subscriptions
    assert broker._user_counts == {}
    assert broker.stats()["total_subscriptions"] == 0


def test_unsubscribe_is_idempotent():
    broker = RunStreamBroker()
    subscription = broker.subscribe("run-1", user_id=7)

    broker.unsubscribe(subscription)
    broker.unsubscribe(subscription)

    assert broker.stats()["total_subscriptions"] == 0


def test_per_run_limit_rejects_subscription():
    broker = RunStreamBroker()
    broker.max_per_run = 1
    broker.subscribe("run-1", user_id=1)

    with pytest.raises(StreamLimitExceededError):
        broker.subscribe("run-1", user_id=2)


def test_per_user_limit_rejects_subscription():
    broker = RunStreamBroker()
    broker.max_per_user = 1
    broker.subscribe("run-1", user_id=7)

    with pytest.raises(StreamLimitExceededError):
        broker.subscribe("run-2", user_id=7)


def test_total_limit_rejects_subscription():
    broker = RunStreamBroker()
    broker.max_total = 1
    broker.subscribe("run-1", user_id=1)

    with pytest.raises(StreamLimitExceededError):
        broker.subscribe("run-2", user_id=2)


def test_slow_consumer_receives_overflow_sentinel_and_stops_delivery():
    broker = RunStreamBroker()
    subscription = broker.subscribe("run-1", user_id=7)
    subscription.queue = asyncio.Queue(maxsize=1)

    broker.publish("run-1", {"type": "log_line", "seq": 1})
    broker.publish("run-1", {"type": "log_line", "seq": 2})  # 队列满 → 溢出

    assert subscription.overflowed is True
    # 溢出时腾位投放哨兵，消费端下一次 get 即可见
    assert subscription.queue.get_nowait() is QUEUE_OVERFLOW
    assert broker.stats()["overflow_disconnects"] == 1

    # 溢出后停止向该订阅者投递
    broker.publish("run-1", {"type": "log_line", "seq": 3})
    assert subscription.queue.empty()


def test_overflow_sentinel_is_first_element_not_behind_backlog():
    """溢出即断：哨兵必须是消费端下一个元素，不能排在积压帧之后。"""
    broker = RunStreamBroker()
    subscription = broker.subscribe("run-1", user_id=7)
    subscription.queue = asyncio.Queue(maxsize=3)

    for seq in range(4):  # 第 4 条触发溢出
        broker.publish("run-1", {"type": "log_line", "seq": seq})

    assert subscription.overflowed is True
    # 积压帧被清空，哨兵是队列中唯一且第一个元素
    assert subscription.queue.get_nowait() is QUEUE_OVERFLOW
    assert subscription.queue.empty()


def test_publish_without_subscribers_is_noop():
    broker = RunStreamBroker()
    broker.publish("run-x", {"type": "log_line"})

    assert broker.stats()["total_subscriptions"] == 0
