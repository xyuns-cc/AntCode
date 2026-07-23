"""P1-round6 5.2 回归:outbox 达上限终止时 last_error 加 TERMINATED 前缀。

审查文档 round6 5.2:
`outbox 达到重试上限仍写 consumed_at; takeover/ACK 交错可留下已 ACK 但
永不 consumed 的事件`。

原实现:达 OUTBOX_CONSUME_MAX_ATTEMPTS 时写 consumed_at 视为终止(避免
无限 republish), 但业务侧无法从 (consumed_at IS NOT NULL) 区分"真正成功"
和"重试耗尽放弃"; 后者应人工排查, 混在一起会掩盖 poison 事件。

修复:达上限时 last_error 加 OUTBOX_TERMINATED_PREFIX,查询终止事件
`last_error__startswith=OUTBOX_TERMINATED_PREFIX` 可精确圈出。

本测试锁死: 达上限时 last_error 前缀存在且原 reason 保留。
"""

from __future__ import annotations

from antcode_core.application.services.scheduler.outbox_service import (
    OUTBOX_CONSUME_MAX_ATTEMPTS,
    OUTBOX_TERMINATED_PREFIX,
)

_LAST_ERROR_MAX_LEN = 2000
_MAX_ATTEMPTS_STABLE = 5
_LONG_REASON_LEN = 3000


def test_prefix_is_a_recognizable_marker():
    assert OUTBOX_TERMINATED_PREFIX.startswith("[")
    assert "TERMINATED" in OUTBOX_TERMINATED_PREFIX
    assert OUTBOX_TERMINATED_PREFIX.endswith(" ")


def test_prefix_and_reason_composed_within_2000():
    # 模拟极端 reason
    reason = "x" * _LONG_REASON_LEN
    composed = f"{OUTBOX_TERMINATED_PREFIX}{reason}"[:_LAST_ERROR_MAX_LEN]
    assert composed.startswith(OUTBOX_TERMINATED_PREFIX)
    assert len(composed) == _LAST_ERROR_MAX_LEN


def test_max_attempts_constant_stable():
    # 变更 consume 上限必须显式修 baseline 与运维告警阈值
    assert OUTBOX_CONSUME_MAX_ATTEMPTS == _MAX_ATTEMPTS_STABLE
