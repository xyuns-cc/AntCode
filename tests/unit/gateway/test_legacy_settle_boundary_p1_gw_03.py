"""P1-GW-03 回归:Gateway 结算 Lua 的 legacy consumer 边界。

审查文档 docs/code-review-2026-07-22-round3-review.md 的 P1-GW-03:
task_settle.py 的 Ack/Requeue Lua legacy 分支(pending[1][2] ~= legacy)
接受"任何"裸 worker_id consumer 结算,没有时间界限。旧代际 Worker 或
攻击者只需构造裸 worker_id 名义就能滑过 lease fence。

修复:引入环境变量 ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS
- 未设/0(默认):兼容旧行为,legacy=worker_id
- 已过时间:legacy=""(Lua 比较不会命中,等同关闭)
- 未过时间:legacy=worker_id(滚动升级窗口内保留)
"""

from __future__ import annotations

import time

import pytest
from antcode_gateway.handlers._settle_legacy_boundary import (
    legacy_settle_argv as _legacy_settle_argv,
)
from antcode_gateway.handlers._settle_legacy_boundary import (
    resolve_legacy_settle_until_ts as _resolve_legacy_settle_until_ts,
)


def test_default_no_env_is_fail_closed(monkeypatch):
    """P1-GW-06 (round6):未设环境变量时默认 fail-closed(生产安全默认)。

    之前 default=0 = 永远开启,生产忘配置就 fail-open。改后 default=None
    = 关闭,滚动升级必须显式设 0 或未来时间戳。
    """
    monkeypatch.delenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", raising=False)
    assert _resolve_legacy_settle_until_ts() is None
    assert _legacy_settle_argv("worker-1") == ""


def test_explicit_zero_opens_legacy_channel(monkeypatch):
    """P1-GW-06 (round6):显式 0 = 永远开启(滚动升级临时窗口)。"""
    monkeypatch.setenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", "0")
    assert _resolve_legacy_settle_until_ts() == 0
    assert _legacy_settle_argv("worker-1") == "worker-1"


def test_future_timestamp_returns_worker_id(monkeypatch):
    """P1-GW-03:滚动升级窗口内(未过截止时间),legacy 保留。"""
    future = int(time.time()) + 3600
    monkeypatch.setenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", str(future))
    assert _legacy_settle_argv("worker-1") == "worker-1"


def test_past_timestamp_returns_empty(monkeypatch):
    """P1-GW-03 关键:已过截止时间 → legacy 关闭(空字符串,Lua 比较不命中)。"""
    past = int(time.time()) - 1
    monkeypatch.setenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", str(past))
    assert _legacy_settle_argv("worker-1") == ""


def test_invalid_env_fails_closed(monkeypatch):
    """P1-GW-06 (round6):非法值 → fail-closed(不再 fail-open,防错配继续开门)。"""
    monkeypatch.setenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", "not-a-number")
    assert _resolve_legacy_settle_until_ts() is None
    assert _legacy_settle_argv("worker-1") == ""


def test_empty_string_env_fails_closed(monkeypatch):
    """P1-GW-06 (round6):空字符串环境变量同样 fail-closed。"""
    monkeypatch.setenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", "")
    assert _resolve_legacy_settle_until_ts() is None
    assert _legacy_settle_argv("worker-1") == ""


@pytest.mark.parametrize("worker_id", ["", "worker-a", "abc-xyz-123"])
def test_arg_returns_empty_when_default_closed(worker_id, monkeypatch):
    """P1-GW-06 (round6):默认关闭时任何 worker_id 都返回空(拒绝 legacy 通道)。"""
    monkeypatch.delenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", raising=False)
    assert _legacy_settle_argv(worker_id) == ""


@pytest.mark.parametrize("worker_id", ["", "worker-a", "abc-xyz-123"])
def test_arg_preserves_worker_id_shape_when_open(worker_id, monkeypatch):
    """P1-GW-06 (round6):显式 0 开启时 legacy 分支透传原 worker_id。"""
    monkeypatch.setenv("ANTCODE_GATEWAY_LEGACY_SETTLE_UNTIL_TS", "0")
    assert _legacy_settle_argv(worker_id) == worker_id
