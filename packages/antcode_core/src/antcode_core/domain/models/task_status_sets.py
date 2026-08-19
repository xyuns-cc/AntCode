"""按语义分组的 TaskStatus 集合。

这些集合只服务于 ORM 查询过滤（``status__in=``），因此归属领域层。
不要放进只依赖 Redis 的服务模块：那会让纯 Redis 路径（如 Spider 存储清理）
被迫导入整个 ORM 模型包，进而实例化控制面 ``Settings()``。

集合分两族，**互不可替代**，新增集合前先判断自己属于哪一族：

- 阻塞判据（``TASK_RUN_TERMINAL_STATUSES`` / ``TASK_RUN_ACTIVE_STATUSES``）
  回答"这条 run 还会不会被继续推进"。多算一个状态只是多拦一次删除、多等一轮
  结算，是 fail-closed 方向；因此 ACTIVE **按终态取补派生**，新增 TaskStatus
  自动落在"仍活跃"一侧，不会因为漏改某一份副本而被当成已结算删掉。
- 许可判据（``SPIDER_WRITABLE_TASK_STATUSES``）回答"允许这条 run 继续做某件
  事"。多算一个状态就是多放行一次，是 fail-open 方向，必须逐个显式列举，
  **不得**改写成取补派生——两族今天的成员差一个 PAUSED，看着像重复，实际上
  安全方向相反。
"""

from antcode_core.domain.models.enums import TaskStatus

# run 已结算：Worker 不会再推进它，删除/清理可以放行。
TASK_RUN_TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.TIMEOUT,
        TaskStatus.SKIPPED,
        TaskStatus.REJECTED,
    }
)

# 终态的补集，按枚举全集派生而非另手写一份，避免新增状态时多处漂移。
# 正向 ``status__in`` 让 ``(task_id, status)`` 复合索引可走范围扫描；
# ``exclude(terminal)`` 在哨兵 task_id 上会退化成扫全部批次 run。
# 唯一定义点：Worker 删除守卫、取消悬挂收敛、临时 Worker 清理、一次性调度
# 兑现判定、并发实例计数、项目删除作用域全部引用这一个对象。
TASK_RUN_ACTIVE_STATUSES = frozenset(TaskStatus) - TASK_RUN_TERMINAL_STATUSES

# Spider 运行期间仍**允许**写入数据的任务状态（许可判据，见模块 docstring）。
SPIDER_WRITABLE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.DISPATCHING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
)

__all__ = [
    "SPIDER_WRITABLE_TASK_STATUSES",
    "TASK_RUN_ACTIVE_STATUSES",
    "TASK_RUN_TERMINAL_STATUSES",
]
