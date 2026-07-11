"""Scheduler RedisQueueBackend 命名空间测试。"""

from antcode_core.application.services.scheduler.redis_queue import RedisQueueBackend


def test_redis_queue_backend_uses_default_namespace():
    backend = RedisQueueBackend(redis_url="redis://localhost:6379/0")

    assert backend._queue_key == "antcode:task_queue"
    assert backend._get_task_data_key("task-1") == "antcode:task_data:task-1"


def test_redis_queue_backend_uses_custom_namespace():
    backend = RedisQueueBackend(redis_url="redis://localhost:6379/0", namespace="ac")

    assert backend._queue_key == "ac:task_queue"
    assert backend._get_task_data_key("task-1") == "ac:task_data:task-1"
