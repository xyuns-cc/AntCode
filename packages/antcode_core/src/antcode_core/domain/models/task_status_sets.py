"""按语义分组的 TaskStatus 集合。

这些集合只服务于 ORM 查询过滤（``status__in=``），因此归属领域层。
不要放进只依赖 Redis 的服务模块：那会让纯 Redis 路径（如 Spider 存储清理）
被迫导入整个 ORM 模型包，进而实例化控制面 ``Settings()``。
"""

from antcode_core.domain.models.enums import TaskStatus

# Spider 运行期间仍可能写入数据的任务状态。
SPIDER_WRITABLE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.DISPATCHING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
)

__all__ = ["SPIDER_WRITABLE_TASK_STATUSES"]
