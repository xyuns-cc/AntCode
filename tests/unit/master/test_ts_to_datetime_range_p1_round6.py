"""P1-round6 5.3 回归:_ts_to_datetime 极值 timestamp 视为未设置。

审查文档 round6 5.3:
`极值 timestamp 不进 DLQ`。

Bug 场景:
- Worker/Gateway 生成的 TaskStatus.started_at.seconds 因编码/内存损坏成 2^62
- Master ingester result_loop 调 _ts_to_datetime → datetime.fromtimestamp
  OverflowError 上抛,消息被 catch 后可能被 ACK 但状态不落库 (started_at
  缺失), 也未进 DLQ,用户看到 run 状态永久 stuck。

修复:_ts_to_datetime 做值域校验(0 <= seconds < ~year 9999, 0 <= nanos <
1e9), 超范或转换异常都 warn + 返回 None; caller/update_result 用
datetime.now(UTC) 兜底。

本测试锁死:
1. 正常 timestamp → 正确 UTC datetime
2. seconds 超 year 9999 → None (不抛)
3. nanos 越界 (>= 1e9 或 <0) → None
4. datetime.fromtimestamp 内部 OverflowError → None (兜底 except)
5. seconds/nanos 都 0 → None (未设置)
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from antcode_master.ingester.result_loop import _ts_to_datetime

_YEAR_2023_SECONDS = 1_700_000_000  # 2023-11-14
_HALF_NANO = 500_000_000
_EXPECTED_YEAR_2023 = 2023
_EXPECTED_YEAR_9999 = 9999
# 253402300799 = 9999-12-31T23:59:59 UTC 上限, 严格 <
_YEAR_9999_LAST_SECOND = 253402300799
_YEAR_9999_LAST_SECOND_MINUS_1 = 253402300798


def test_ts_valid_returns_utc_datetime():
    ts = SimpleNamespace(seconds=_YEAR_2023_SECONDS, nanos=_HALF_NANO)
    dt = _ts_to_datetime(ts)
    assert dt is not None
    assert dt.tzinfo == UTC
    assert dt.year == _EXPECTED_YEAR_2023


def test_ts_all_zero_returns_none_as_unset():
    ts = SimpleNamespace(seconds=0, nanos=0)
    assert _ts_to_datetime(ts) is None


def test_ts_none_returns_none():
    assert _ts_to_datetime(None) is None


def test_ts_seconds_beyond_year_9999_returns_none():
    # 2^62 seconds ≈ year 1.46e11, 远超 datetime 上限
    ts = SimpleNamespace(seconds=2**62, nanos=0)
    assert _ts_to_datetime(ts) is None  # 值域校验拦下, 不 raise


def test_ts_negative_seconds_returns_none():
    ts = SimpleNamespace(seconds=-1, nanos=0)
    assert _ts_to_datetime(ts) is None


def test_ts_negative_nanos_returns_none():
    ts = SimpleNamespace(seconds=_YEAR_2023_SECONDS, nanos=-1)
    assert _ts_to_datetime(ts) is None


def test_ts_nanos_overflow_returns_none():
    # nanos 语义 [0, 1e9), 1e9 就是非法
    ts = SimpleNamespace(seconds=_YEAR_2023_SECONDS, nanos=1_000_000_000)
    assert _ts_to_datetime(ts) is None


def test_ts_seconds_at_upper_bound_edge():
    ts = SimpleNamespace(seconds=_YEAR_9999_LAST_SECOND, nanos=0)
    # 边界值不允许 (< 判断), 返回 None
    assert _ts_to_datetime(ts) is None
    # 稍小一秒应该 pass
    ts2 = SimpleNamespace(seconds=_YEAR_9999_LAST_SECOND_MINUS_1, nanos=0)
    dt = _ts_to_datetime(ts2)
    assert dt is not None
    assert dt.year == _EXPECTED_YEAR_9999


def test_ts_missing_attributes_treated_as_zero():
    # getattr default = 0
    ts = SimpleNamespace()
    assert _ts_to_datetime(ts) is None
