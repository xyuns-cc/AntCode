"""触发器必须绑定 ``SCHEDULER_TIMEZONE``，而不是容器的系统本地时区。

走查实测：``SCHEDULER_TIMEZONE=Asia/Shanghai`` 的部署里，cron ``0 3 * * *``
的下次触发时间是 ``2026-08-20 03:00:00+00:00``（北京时间 11:00）——晚 8 小时。
根因是 APScheduler 只对「自己构造的 trigger」套用 scheduler 默认时区；
``create_task_trigger`` 传入的是构造好的 trigger 对象，而
``CronTrigger.from_crontab()`` 不带 timezone 时退到系统本地时区（容器为 UTC）。
"""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from antcode_core.domain.models.enums import ScheduleType
from antcode_master.control import durable_schedule

SHANGHAI = "Asia/Shanghai"
EIGHT_HOURS = timedelta(hours=8)
EXPECTED_LOCAL_HOUR = 3


@pytest.fixture(autouse=True)
def _shanghai_scheduler(monkeypatch) -> None:
    monkeypatch.setattr(durable_schedule.settings, "SCHEDULER_TIMEZONE", SHANGHAI)


def _task(schedule_type, **fields) -> SimpleNamespace:
    base = {
        "cron_expression": None,
        "interval_seconds": None,
        "scheduled_time": None,
    }
    return SimpleNamespace(schedule_type=schedule_type, **{**base, **fields})


def test_cron_trigger_binds_configured_timezone() -> None:
    trigger = durable_schedule.create_task_trigger(_task(ScheduleType.CRON, cron_expression="0 3 * * *"))

    assert str(trigger.timezone) == SHANGHAI


def test_cron_next_fire_time_is_local_three_am_not_utc() -> None:
    trigger = durable_schedule.create_task_trigger(_task(ScheduleType.CRON, cron_expression="0 3 * * *"))
    now = datetime(2026, 8, 19, 20, 0, tzinfo=ZoneInfo(SHANGHAI))

    fire_time = trigger.get_next_fire_time(None, now)

    # 判据：偏移必须是 +08:00 且本地墙钟为 03:00。绑错时区时这里会是 +00:00。
    assert fire_time.utcoffset() == EIGHT_HOURS
    assert fire_time.astimezone(ZoneInfo(SHANGHAI)).hour == EXPECTED_LOCAL_HOUR
    assert fire_time != datetime(2026, 8, 20, 3, 0, tzinfo=UTC)


def test_interval_trigger_binds_configured_timezone() -> None:
    trigger = durable_schedule.create_task_trigger(_task(ScheduleType.INTERVAL, interval_seconds=300))

    assert str(trigger.timezone) == SHANGHAI


def test_date_trigger_binds_configured_timezone() -> None:
    trigger = durable_schedule.create_task_trigger(_task(ScheduleType.DATE, scheduled_time=datetime(2026, 8, 20, 3, 0)))

    assert str(trigger.run_date.tzinfo) == SHANGHAI
    assert trigger.run_date.utcoffset() == EIGHT_HOURS


def test_once_without_scheduled_time_lands_on_the_current_instant() -> None:
    """ONCE 缺省触发时刻必须是"现在"这个瞬间。

    注意：这**不是**证伪用例——摘掉本文件的修复后它依然绿。原实现的
    ``datetime.now()`` 是 naive 值，会被 trigger 按其时区再解释一次；只有当
    运行环境本地时区 != SCHEDULER_TIMEZONE 时才偏移，而开发机与目标时区
    同为 UTC+8，本地跑不出差异。这里只作为回归护栏保留。
    """
    before = datetime.now(UTC)
    trigger = durable_schedule.create_task_trigger(_task(ScheduleType.ONCE))
    after = datetime.now(UTC)

    assert before <= trigger.run_date <= after
