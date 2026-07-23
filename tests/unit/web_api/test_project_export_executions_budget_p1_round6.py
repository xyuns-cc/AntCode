"""P1-round6 5.3 回归:项目导出 execution 字段字节预算。

审查文档 round6 5.3:
`项目导出预算只覆盖日志,不覆盖最多 200 条 execution 的
result_data/error/stdout/stderr`。

修复:bound_execution_export_payloads 逐条累加 execution 的可膨胀字段
(result_data/error_message/stdout/stderr) UTF-8 字节, 超预算的 execution
保留元数据但把这些字段替换成 truncated 标记, 顶层 executions_truncated
显式暴露。

本测试锁死:
1. 小体量 execution 列表 → 不裁剪, executions_truncated=False
2. 单条超预算 → 该条字段清空 + truncated=True, 后续条目继续按剩余预算处理
3. 累加超预算 → 后续条目被清
4. 无法 JSON 序列化的 result_data → 保守判定超预算并截断
5. 空列表 → False
"""

from __future__ import annotations

from antcode_web_api.routes.v1.project_export_executions import (
    EXPORT_EXECUTION_MAX_TOTAL_BYTES,
    bound_execution_export_payloads,
)


def _make_exec(idx: int, big_field: str = "", big_key: str = "stdout") -> dict:
    exec_dict = {
        "id": f"exec-{idx}",
        "run_id": f"run-{idx}",
        "error_message": "",
        "stdout": "",
        "stderr": "",
        "result_data": {},
    }
    if big_field:
        exec_dict[big_key] = big_field
    return exec_dict


def test_empty_list_returns_false():
    assert bound_execution_export_payloads([]) is False


def test_small_execution_list_not_truncated():
    execs = [_make_exec(i, "small") for i in range(5)]
    truncated = bound_execution_export_payloads(execs)
    assert truncated is False
    for e in execs:
        assert e["stdout"] == "small"


def test_accumulated_over_budget_truncates_tail():
    # 每条 3 MiB stdout, 累加 3 条 = 9 MiB 超过 8 MiB
    big = "x" * (3 * 1024 * 1024)
    execs = [_make_exec(i, big) for i in range(3)]
    truncated = bound_execution_export_payloads(execs)
    assert truncated is True
    # 前 2 条正常, 第 3 条被清
    assert execs[0]["stdout"] == big
    assert execs[1]["stdout"] == big
    assert "truncated" in execs[2]["stdout"]
    assert execs[2]["result_data"] == {"_truncated": True}


def test_single_oversized_execution_gets_blanked_alone():
    # 单条 10 MiB stdout, 直接超 8 MiB 预算 → 只有该条被清, 后续 execution 仍能进入
    over_budget = "y" * (10 * 1024 * 1024)
    execs = [_make_exec(0, over_budget), _make_exec(1, "small"), _make_exec(2, "tiny")]
    truncated = bound_execution_export_payloads(execs)
    assert truncated is True
    assert "truncated" in execs[0]["stdout"]
    # 后续 execution 走剩余预算, 不受第一条超限影响
    assert execs[1]["stdout"] == "small"
    assert execs[2]["stdout"] == "tiny"


def test_non_serializable_result_data_triggers_truncation():
    circular: dict = {}
    circular["self"] = circular
    execs = [
        {
            "id": "e-1",
            "run_id": "r-1",
            "error_message": "",
            "stdout": "",
            "stderr": "",
            "result_data": circular,
        }
    ]
    truncated = bound_execution_export_payloads(execs)
    assert truncated is True
    assert execs[0]["result_data"] == {"_truncated": True}


def test_budget_constant_matches_spec():
    assert EXPORT_EXECUTION_MAX_TOTAL_BYTES == 8 * 1024 * 1024
