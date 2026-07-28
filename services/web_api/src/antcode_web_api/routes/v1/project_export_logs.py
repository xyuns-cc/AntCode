"""项目导出的 TaskLog 加载（P1-SEC-03：总字节预算约束）。

从 ``project.py`` 拆出的独立模块（复杂度门禁 FILE_LINES 约束）。
此前仅有 200 run × 200 行的行数上限，单行可达 ~1MiB 时导出体积可接近
40GiB；本模块逐行累计 content 的 UTF-8 字节，预算耗尽立即停止读库，
并把截断显式暴露给调用方（路由写入 ``task_logs_truncated`` 顶层字段）。
"""

from __future__ import annotations

from typing import Any

# 每个 run 导出的日志行数上限（取最新行），防止导出体积失控。
EXPORT_LOG_LINES_PER_RUN = 200
# P1-SEC-03: 全部 run 日志 content 的总字节预算。
EXPORT_LOG_MAX_TOTAL_BYTES = 8 * 1024 * 1024


async def load_export_task_logs(run_ids: list[str]) -> tuple[dict[str, dict[str, Any]], bool]:
    """按总字节预算加载各 run 的导出日志。

    Returns:
        (task_logs, truncated_by_budget)：预算耗尽导致任何行/run 被裁掉时
        第二项为 True（预算耗尽后剩余 run 不再读库，直接整体省略）。
    """
    task_logs: dict[str, dict[str, Any]] = {}
    budget = EXPORT_LOG_MAX_TOTAL_BYTES
    truncated_by_budget = False
    for run_id in run_ids:
        if budget <= 0:
            # 预算已耗尽且仍有 run 未处理：保守标记截断（不再读库确认剩余
            # run 是否真的有日志，避免预算耗尽后继续产生 DB 负载）。
            truncated_by_budget = True
            break
        run_payload, budget, run_hit_budget = await _load_one_run_export_logs(run_id, budget)
        truncated_by_budget = truncated_by_budget or run_hit_budget
        if run_payload is not None:
            task_logs[run_id] = run_payload
    return task_logs, truncated_by_budget


async def _load_one_run_export_logs(
    run_id: str,
    budget: int,
) -> tuple[dict[str, Any] | None, int, bool]:
    """加载单个 run 的导出日志，逐行扣减字节预算。

    Returns:
        (payload, 剩余预算, 是否因预算截断)。run 无日志时 payload 为 None。
    """
    from antcode_core.application.services.logs.postgres_log_service import postgres_log_service

    entries = await postgres_log_service.list_entries(run_id, limit=EXPORT_LOG_LINES_PER_RUN, latest=True)
    if not entries:
        return None, budget, False
    total = await postgres_log_service.count(run_id)
    lines: list[dict[str, Any]] = []
    hit_budget = False
    for entry in entries:
        content = entry.content or ""
        cost = len(content.encode("utf-8"))
        if cost > budget:
            hit_budget = True
            break
        budget -= cost
        lines.append(
            {
                "log_type": entry.log_type,
                "content": content,
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else "",
                "sequence": entry.sequence,
                "level": entry.level,
            }
        )
    payload = {
        "truncated": total > len(lines),
        "total_lines": total,
        "lines": lines,
    }
    return payload, budget, hit_budget


__all__ = [
    "EXPORT_LOG_LINES_PER_RUN",
    "EXPORT_LOG_MAX_TOTAL_BYTES",
    "load_export_task_logs",
]
