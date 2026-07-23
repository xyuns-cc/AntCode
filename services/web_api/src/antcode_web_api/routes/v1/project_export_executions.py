"""项目导出的 execution 字段字节预算 (P1-round6 5.3)。

审查文档 round6 5.3:
`项目导出预算只覆盖日志,不覆盖最多 200 条 execution 的
result_data/error/stdout/stderr`。

_load_export_executions 返回 200 条 TaskRunResponse dict,每条含
result_data(JSONB, 单帧上限 1 MiB) + error_message + stdout + stderr,
理论峰值 200 * (result_data + error + stdout + stderr) 可达几 GB 内存
拷贝(json.dumps + Response 返回), 之前只有日志走了字节预算。

本模块给 executions 独立 8 MiB 字节预算, 逐条累加序列化后的四个字段;
超预算就把后续 execution 的这些字段清空并追加 marker, 与 task_logs 的
truncated 标记同层暴露(payload["executions_truncated"])。
"""

from __future__ import annotations

import json
from typing import Any

EXPORT_EXECUTION_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_EXEC_TRUNCATED_MARKER = "… (truncated by export payload size budget)"


def _payload_field_bytes(execution: dict[str, Any]) -> int:
    """计算单条 execution 的可膨胀字段合计字节数 (UTF-8)。"""
    total = 0
    total += len((execution.get("error_message") or "").encode("utf-8"))
    total += len((execution.get("stdout") or "").encode("utf-8"))
    total += len((execution.get("stderr") or "").encode("utf-8"))
    result_data = execution.get("result_data") or {}
    if result_data:
        try:
            total += len(json.dumps(result_data, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            # 无法序列化时保守当作超预算字段(比预算大 1 字节保证触发截断)
            total += EXPORT_EXECUTION_MAX_TOTAL_BYTES + 1
    return total


def _blank_execution_fields(execution: dict[str, Any]) -> None:
    """在原 dict 上把可膨胀字段清空并标记 truncated (幂等)。"""
    execution["error_message"] = _EXEC_TRUNCATED_MARKER
    execution["stdout"] = _EXEC_TRUNCATED_MARKER
    execution["stderr"] = _EXEC_TRUNCATED_MARKER
    execution["result_data"] = {"_truncated": True}


def bound_execution_export_payloads(executions: list[dict[str, Any]]) -> bool:
    """按字节预算裁剪 execution 列表 (原地修改)。

    Returns:
        truncated: 是否有任何 execution 被裁剪。
    """
    if not executions:
        return False
    budget = EXPORT_EXECUTION_MAX_TOTAL_BYTES
    truncated = False
    for execution in executions:
        cost = _payload_field_bytes(execution)
        if cost > budget:
            _blank_execution_fields(execution)
            truncated = True
            continue
        budget -= cost
    return truncated


__all__ = [
    "EXPORT_EXECUTION_MAX_TOTAL_BYTES",
    "bound_execution_export_payloads",
]
