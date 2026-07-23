"""P1-LOG (round6 5.3) merge_result_data 总字节 cap 回归。

审查文档 round6 5.3:`result_data 只有 1 MiB 单帧限制, 同一 run 可通过
不同 key 无界扩大 Redis/JSONB/WAL`。修复:merge_result_data 合并后 JSON
字节超 2 MiB 时 warn + 丢弃本次 update, 保留 current 不破坏语义。

本测试锁死:
1. 正常 merge (小 payload) 仍工作
2. 单次 update 就超上限 → 丢弃 update,返回原 current
3. 累积 merge 逼近上限时下一次 update 被拒
4. update 无法 JSON 序列化时保守返回 current
5. update=None/{} 直接返回 current 副本
"""

from __future__ import annotations

import pytest
from antcode_master.control.result_metadata import (
    _MAX_RESULT_DATA_BYTES,
    merge_result_data,
)


def test_normal_merge_within_budget():
    """P1-LOG: 正常合并小 payload 仍能更新字段。"""
    current = {"phase": "created", "attempt": 1}
    update = {"phase": "running", "worker_id": 42}
    merged = merge_result_data(current, update)
    assert merged == {"phase": "running", "attempt": 1, "worker_id": 42}


def test_single_update_over_budget_dropped():
    """P1-LOG: 单次 update 就超上限 → 保留 current,丢弃 update。"""
    current = {"attempt": 1}
    # 构造一个明显超 2 MiB 的 update
    huge = "x" * (_MAX_RESULT_DATA_BYTES + 1000)
    update = {"payload": huge}
    merged = merge_result_data(current, update)
    # 原字段保留, huge 字段未被合并
    assert merged == {"attempt": 1}
    assert "payload" not in merged


def test_accumulated_merge_near_budget_next_update_rejected():
    """P1-LOG: 累积 merge 逼近上限,下一次超阈值的 update 被拒。"""
    # 先塞一个大 payload 但仍在 budget 内
    near_limit = "y" * (_MAX_RESULT_DATA_BYTES // 2)
    current = merge_result_data({}, {"chunk1": near_limit})
    assert "chunk1" in current

    # 再来一个大 payload,合并后 > 2 MiB
    another_big = "z" * (_MAX_RESULT_DATA_BYTES // 2 + 5000)
    merged = merge_result_data(current, {"chunk2": another_big})
    # chunk2 被拒, chunk1 保留
    assert "chunk1" in merged
    assert "chunk2" not in merged


def test_update_not_serializable_falls_back_to_current():
    """P1-LOG: update 无法 JSON 序列化时保守返回 current,不 crash。"""

    class NotSerializable:
        pass

    current = {"phase": "created"}
    # NotSerializable 走 default=str 会得到 <obj at 0x...>,不 raise
    # 但如果我们强制传含 circular ref,dumps 会 ValueError
    circular: dict = {}
    circular["self"] = circular
    merged = merge_result_data(current, circular)
    assert merged == {"phase": "created"}


@pytest.mark.parametrize("update", [None, {}])
def test_empty_update_returns_current_copy(update):
    """P1-LOG: update=None/{} 直接返回 current 副本。"""
    current = {"phase": "queued", "attempt": 2}
    merged = merge_result_data(current, update)
    assert merged == current
    # 是副本, 修改 merged 不影响 current
    merged["mutated"] = True
    assert "mutated" not in current


def test_current_none_treated_as_empty():
    """P1-LOG: current=None 视为空,仅返回 update 合并后的 dict。"""
    merged = merge_result_data(None, {"phase": "success"})
    assert merged == {"phase": "success"}


def test_max_budget_constant_is_reasonable():
    """P1-LOG: 上限至少 1 MiB(容纳合法大 result), 不超 8 MiB(防 WAL 冲击)。"""
    min_bytes = 1 * 1024 * 1024
    max_bytes = 8 * 1024 * 1024
    assert min_bytes <= _MAX_RESULT_DATA_BYTES <= max_bytes
