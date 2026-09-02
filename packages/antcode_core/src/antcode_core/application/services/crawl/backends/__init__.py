"""爬虫 Redis 后端抽象层。"""

from antcode_core.application.services.crawl.backends.progress_backend import (
    ProgressStore,
    get_progress_store,
    reset_progress_store,
)
from antcode_core.application.services.crawl.backends.redis_progress import RedisProgressStore

__all__ = [
    "ProgressStore",
    "RedisProgressStore",
    "get_progress_store",
    "reset_progress_store",
]
