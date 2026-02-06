"""
爬取批次模型

爬取批次的数据模型定义。
"""

from tortoise import fields

from antcode_core.domain.models.base import BaseModel, generate_public_id
from antcode_core.domain.models.enums import BatchStatus


class CrawlTaskStatus:
    """爬取任务状态常量

    状态流转规则:
    - PENDING → DISPATCHED: 任务分发给 Worker
    - DISPATCHED → RUNNING: Worker 开始执行
    - RUNNING → SUCCESS: 任务执行成功
    - RUNNING → RETRY: 任务执行失败但可重试
    - RUNNING → TIMEOUT: 任务执行超时
    - RETRY → DISPATCHED: 重试任务重新分发
    - TIMEOUT → DISPATCHED: 超时任务重新分发（通过 XCLAIM）
    - RETRY → FAILED: 重试次数超限
    - TIMEOUT → FAILED: 超时次数超限
    """

    PENDING = "pending"        # 等待分发
    DISPATCHED = "dispatched"  # 已分发给 Worker
    RUNNING = "running"        # Worker 正在执行
    SUCCESS = "success"        # 执行成功
    RETRY = "retry"            # 需要重试
    TIMEOUT = "timeout"        # 执行超时
    FAILED = "failed"          # 最终失败（进入死信队列）

    # 有效的状态转换映射
    VALID_TRANSITIONS = {
        PENDING: [DISPATCHED],
        DISPATCHED: [RUNNING, TIMEOUT],
        RUNNING: [SUCCESS, RETRY, TIMEOUT],
        RETRY: [DISPATCHED, FAILED],
        TIMEOUT: [DISPATCHED, FAILED],
        SUCCESS: [],  # 终态
        FAILED: [],   # 终态
    }

    # 终态列表
    TERMINAL_STATES = [SUCCESS, FAILED]

    # 可重试状态列表
    RETRYABLE_STATES = [RETRY, TIMEOUT]

    @classmethod
    def is_valid_transition(cls, from_status: str, to_status: str) -> bool:
        """检查状态转换是否有效"""
        valid_targets = cls.VALID_TRANSITIONS.get(from_status, [])
        return to_status in valid_targets

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        """检查是否为终态"""
        return status in cls.TERMINAL_STATES

    @classmethod
    def is_retryable(cls, status: str) -> bool:
        """检查是否为可重试状态"""
        return status in cls.RETRYABLE_STATES


class CrawlBatch(BaseModel):
    """爬取批次模型"""

    public_id = fields.CharField(
        max_length=32, unique=True, default=generate_public_id, db_index=True
    )
    project_id = fields.BigIntField(index=True, description="关联项目ID")
    name = fields.CharField(max_length=255, description="批次名称")
    description = fields.TextField(null=True, description="批次描述")

    # 种子配置
    seed_urls = fields.JSONField(default=list, description="种子URL列表")

    # 爬取配置
    max_depth = fields.IntField(default=3, description="最大爬取深度")
    max_pages = fields.IntField(default=10000, description="最大爬取页面数")
    max_concurrency = fields.IntField(default=50, description="最大并发数")
    request_delay = fields.FloatField(default=0.5, description="请求间隔(秒)")
    timeout = fields.IntField(default=30, description="请求超时(秒)")
    max_retries = fields.IntField(default=3, description="最大重试次数")

    # 状态
    status = fields.CharField(
        max_length=20, default=BatchStatus.PENDING.value, description="批次状态"
    )
    is_test = fields.BooleanField(default=False, description="是否为测试批次")

    # 时间
    created_at = fields.DatetimeField(auto_now_add=True, description="创建时间")
    started_at = fields.DatetimeField(null=True, description="开始时间")
    completed_at = fields.DatetimeField(null=True, description="完成时间")

    # 创建者
    user_id = fields.BigIntField(description="创建者ID")

    class Meta:
        table = "crawl_batches"
        indexes = [
            ("project_id",),
            ("status",),
            ("user_id",),
            ("is_test",),
            ("created_at",),
            ("project_id", "status"),
            ("user_id", "status"),
        ]

    def __str__(self):
        return f"CrawlBatch({self.public_id}: {self.name})"


__all__ = [
    "CrawlTaskStatus",
    "CrawlBatch",
]
