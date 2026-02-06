"""爬虫后端抽象层

提供队列、去重、进度存储的抽象接口和工厂方法。
"""

from antcode_core.application.services.crawl.backends.base import (
    CrawlQueueBackend,
    QueueMetrics,
    QueueStats,
    QueueTask,
    ReclaimedTask,
    get_queue_backend,
    reset_queue_backend,
)
from antcode_core.application.services.crawl.backends.dedup_backend import (
    DedupStore,
    get_dedup_store,
    reset_dedup_store,
)
from antcode_core.application.services.crawl.backends.memory_dedup import InMemoryDedupStore
from antcode_core.application.services.crawl.backends.memory_progress import InMemoryProgressStore
from antcode_core.application.services.crawl.backends.memory_queue import InMemoryCrawlQueueBackend
from antcode_core.application.services.crawl.backends.progress_backend import (
    ProgressStore,
    get_progress_store,
    reset_progress_store,
)
from antcode_core.application.services.crawl.backends.redis_dedup import RedisDedupStore, get_dedup_key
from antcode_core.application.services.crawl.backends.redis_progress import RedisProgressStore
from antcode_core.application.services.crawl.backends.redis_queue import (
    DEFAULT_CONSUMER_GROUP,
    RedisCrawlQueueBackend,
    get_all_priority_keys,
    get_dead_letter_key,
    get_stream_key,
)

__all__ = [
    # 队列抽象基类
    "CrawlQueueBackend",
    # 去重抽象基类
    "DedupStore",
    # 进度抽象基类
    "ProgressStore",
    # 数据类
    "QueueStats",
    "QueueMetrics",
    "QueueTask",
    "ReclaimedTask",
    # 队列工厂方法
    "get_queue_backend",
    "reset_queue_backend",
    # 去重工厂方法
    "get_dedup_store",
    "reset_dedup_store",
    # 进度工厂方法
    "get_progress_store",
    "reset_progress_store",
    # 队列具体实现
    "InMemoryCrawlQueueBackend",
    "RedisCrawlQueueBackend",
    # 去重具体实现
    "InMemoryDedupStore",
    "RedisDedupStore",
    # 进度具体实现
    "InMemoryProgressStore",
    "RedisProgressStore",
    # Redis 键名工具函数
    "get_stream_key",
    "get_dead_letter_key",
    "get_all_priority_keys",
    "get_dedup_key",
    "DEFAULT_CONSUMER_GROUP",
]
