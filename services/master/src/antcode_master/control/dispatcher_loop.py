"""Master 对核心 Rule dispatcher 的稳定导出。"""

from antcode_core.application.services.scheduler.spider_dispatcher import (
    SpiderTaskDispatcher,
    spider_task_dispatcher,
)

__all__ = ["SpiderTaskDispatcher", "spider_task_dispatcher"]
