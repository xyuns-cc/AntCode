"""批次 run 状态聚合查询。

从 ``crawl_batch_status_loop`` 抽出：读侧 SQL 与终态字面量是一个自洽的单元，
而 loop 文件已经顶在 300 行硬上限之外，再往里加东西只会继续恶化。
"""

from __future__ import annotations

from antcode_core.domain.models.enums import TaskStatus

# 终态集合与 execution_status_service 保持一致
_RUN_TERMINAL_STATES = (
    TaskStatus.SUCCESS,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.TIMEOUT,
    TaskStatus.SKIPPED,
    TaskStatus.REJECTED,
)

# T7-B2b: 聚合 SQL 里用字面量字符串（TaskStatus.value）
_TERMINAL_STR = tuple(state.value for state in _RUN_TERMINAL_STATES)
_SUCCESS_STR = TaskStatus.SUCCESS.value
_CANCELLED_STR = TaskStatus.CANCELLED.value
_FAILED_LIKE_STR = (TaskStatus.FAILED.value, TaskStatus.TIMEOUT.value, TaskStatus.REJECTED.value)

_BATCH_STATS_SQL = f"""
    SELECT
        result_data->>'crawl_batch_id' AS batch_id,
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE status = '{_SUCCESS_STR}') AS success,
        COUNT(*) FILTER (WHERE status IN {_FAILED_LIKE_STR}) AS failed,
        COUNT(*) FILTER (WHERE status = '{_CANCELLED_STR}') AS cancelled,
        COUNT(*) FILTER (WHERE status NOT IN {_TERMINAL_STR}) AS active,
        COUNT(DISTINCT worker_id) FILTER (
            WHERE status NOT IN {_TERMINAL_STR} AND worker_id IS NOT NULL
        ) AS active_workers
    FROM task_executions
    WHERE result_data->>'crawl_batch_id' = ANY($1)
    GROUP BY result_data->>'crawl_batch_id'
"""

_COUNT_COLUMNS = ("total", "success", "failed", "cancelled", "active", "active_workers")


async def fetch_batch_stats(batch_ids: list[str]) -> dict[str, dict[str, int]]:
    """一次拉出所有 batch 的 run 状态计数；无 run 的 batch 不返回。"""
    if not batch_ids:
        return {}
    from tortoise import connections

    conn = connections.get("default")
    _, rows = await conn.execute_query(_BATCH_STATS_SQL, [batch_ids])
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        batch_id = row.get("batch_id")
        if not batch_id:
            continue
        out[batch_id] = {column: int(row.get(column, 0)) for column in _COUNT_COLUMNS}
    return out
