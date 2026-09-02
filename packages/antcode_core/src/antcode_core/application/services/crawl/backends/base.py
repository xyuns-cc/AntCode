"""爬虫 Redis 后端共用的存储约束。"""

import os

from antcode_core.common.settings_ref import current_settings


def _redis_backend_required(*env_names: str) -> None:
    for env_name in env_names:
        value = os.getenv(env_name, "").strip().lower()
        if value and value != "redis":
            raise ValueError("爬虫队列只能使用 Redis")
    if current_settings().CRAWL_BACKEND.strip().lower() != "redis":
        raise ValueError("爬虫队列只能使用 Redis")
