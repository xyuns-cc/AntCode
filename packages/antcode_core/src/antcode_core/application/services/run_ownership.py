"""TaskRun 所有者解析。

``TaskRun`` 有两类，所有者来自两张不同的表，任何鉴权入口都必须分开解析：

- 计划任务 run：owner = ``Task.user_id``；
- 爬取批次 run：``task_id`` 恒为 ``TASK_ID_ABSENT`` 哨兵，``scheduled_tasks`` 里永远没有
  对应行，owner = ``CrawlBatch.user_id``，批次身份取自 ``result_data["crawl_batch_id"]``。

只按 ``Task`` 解析会让批次 run 的所有者永远解析不出来，用户对自己的批次 run 一律拿到 404。
契约与 ``workers/dispatch_authorization.py``、``workers/spider_run_access.py`` 一致。
"""

from __future__ import annotations

from typing import Any

from antcode_core.domain.models import CrawlBatch, Task
from antcode_core.domain.models.task_run import TASK_ID_ABSENT


async def resolve_run_owner_id(run: Any) -> int | None:
    """解析 run 的真实所有者 user_id；解析不出返回 ``None``。

    返回 ``None`` 只有一个含义：**这个 run 没有可信的所有者**，调用方必须按无权拒绝。
    哨兵豁免只免掉"批次 run 去 ``scheduled_tasks`` 查 id=0"这件注定失败的事，不豁免归属：
    - 真丢 Task 行的孤儿计划任务 run → ``None``（照旧拒绝）；
    - 批次行已删除或 ``crawl_batch_id`` 不合法的批次 run → ``None``（照旧拒绝）。
    """
    if int(run.task_id) != TASK_ID_ABSENT:
        task = await Task.filter(id=run.task_id).only("id", "user_id").first()
        return None if task is None else int(task.user_id)
    batch_id = batch_id_of_run(run)
    if batch_id is None:
        return None
    batch = await CrawlBatch.filter(public_id=batch_id).only("id", "user_id").first()
    return None if batch is None else int(batch.user_id)


def batch_id_of_run(run: Any) -> str | None:
    """取批次 run 的 ``crawl_batch_id``；非批次 run 或取不到合法值返回 ``None``。

    批次身份的**唯一**提取口。除所有者解析外，级联删除的"项目是否还有在途批次
    run"判定也走这里，避免同一件事出现第二份写法。
    """
    result_data = run.result_data
    batch_id = result_data.get("crawl_batch_id") if isinstance(result_data, dict) else None
    if not isinstance(batch_id, str) or not batch_id.strip():
        return None
    return batch_id


__all__ = ["batch_id_of_run", "resolve_run_owner_id"]
