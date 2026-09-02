"""爬虫 Redis 后端抽象层。"""

from antcode_core.application.services.crawl.backends.dedup_backend import (
    DedupStore,
    get_dedup_store,
    reset_dedup_store,
)
from antcode_core.application.services.crawl.backends.progress_backend import (
    ProgressStore,
    get_progress_store,
    reset_progress_store,
)
from antcode_core.application.services.crawl.backends.redis_dedup import RedisDedupStore, get_dedup_key
from antcode_core.application.services.crawl.backends.redis_progress import RedisProgressStore

__all__ = [
    # 去重抽象基类
    "DedupStore",
    # 进度抽象基类
    "ProgressStore",
    # 去重工厂方法
    "get_dedup_store",
    "reset_dedup_store",
    # 进度工厂方法
    "get_progress_store",
    "reset_progress_store",
    # 去重具体实现
    "RedisDedupStore",
    # 进度具体实现
    "RedisProgressStore",
    # Redis 键名工具函数
    "get_dedup_key",
]
