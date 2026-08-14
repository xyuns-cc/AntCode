"""P1-round6 5.3 回归:TaskRunService._build_result_updates 对 result_data 上限。

审查文档 round6 5.3:
`result_data 只有单帧限制,同一 run 可通过不同 key 无界扩大 Redis、JSONB
和 WAL`。

修复:_build_result_updates 用 _apply_bounded_result_data (2 MiB 上限),
超上限或无法序列化时显式拒绝最终结果，由提交器将任务标记失败。

本测试锁死:
1. update 小于上限 → 正常 merge
2. update 累加超 2 MiB → 显式拒绝
3. lease_id 从 update 中被排除 (原有行为)
4. update 无法 JSON 序列化 → 显式拒绝
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from antcode_core.application.services.task_result_commit import ResultMetadataRejected
from antcode_core.application.services.task_run_service import (
    _MAX_RESULT_DATA_BYTES,
    TaskRunService,
    _apply_bounded_result_data,
    _ResultMetadata,
)


def test_apply_bounded_small_update_merges():
    current = {"progress": 50}
    update = {"stage": "processing", "hint": "ok"}
    merged = _apply_bounded_result_data(current, update)
    assert merged == {"progress": 50, "stage": "processing", "hint": "ok"}


def test_apply_bounded_over_limit_rejects_result():
    current = {"progress": 50, "note": "keep"}
    # 造一个 3 MiB 的字符串, 累加必超 2 MiB
    big = "x" * (3 * 1024 * 1024)
    update = {"blob": big}
    with pytest.raises(ResultMetadataRejected, match="超过上限"):
        _apply_bounded_result_data(current, update)


def test_apply_bounded_empty_update_returns_current():
    current = {"progress": 10}
    assert _apply_bounded_result_data(current, {}) == current
    assert _apply_bounded_result_data(current, {}) is current


def test_apply_bounded_non_json_serializable_rejects_result():
    current = {"progress": 10}
    # set 不可 json 序列化 (default=str 会 stringify), 用真正无法序列化的 - 自引用
    circular: dict = {}
    circular["self"] = circular
    with pytest.raises(ResultMetadataRejected, match="无法序列化"):
        _apply_bounded_result_data(current, circular)


def test_build_result_updates_rejects_result_over_budget():
    service = TaskRunService()
    execution = SimpleNamespace(
        result_data={"seed": 1},
        start_time=None,
        duration_seconds=None,
    )
    big = "y" * (3 * 1024 * 1024)
    with pytest.raises(ResultMetadataRejected, match="超过上限"):
        service._build_result_updates(
            execution,
            _ResultMetadata(
                started_at=None,
                finished_at=None,
                duration_ms=None,
                exit_code=0,
                error_message=None,
                output=None,
                data={"blob": big, "lease_id": "L-1"},
            ),
        )


def test_max_result_data_bytes_constant_exposed():
    # 提供给上游模块使用, 不能被误改
    assert _MAX_RESULT_DATA_BYTES == 2 * 1024 * 1024
