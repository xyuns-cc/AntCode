"""Redis Key 命名规范

定义统一的 Redis Key 命名规则，避免 Key 冲突。
"""


class RedisKeys:
    """Redis Key 命名空间"""

    DEFAULT_NAMESPACE = "antcode"

    # === 任务相关 ===
    TASK_READY_PREFIX = "task:ready"
    TASK_RESULT_PREFIX = "task:result"

    # === 日志相关 ===
    LOG_STREAM_PREFIX = "log:stream"

    # === Worker 相关 ===
    WORKER_HEARTBEAT_PREFIX = "worker:heartbeat"

    # === Spider 相关 ===
    SPIDER_INDEX_PREFIX = "spider:index"
    SPIDER_CONFIG_PREFIX = "spider:config"

    def __init__(self, namespace: str | None = None) -> None:
        self.namespace = namespace or self.DEFAULT_NAMESPACE

    def _ns(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def _slot(self, key: str) -> str:
        return f"{{{self.namespace}}}:{key}"

    # === 命名空间实例方法 ===
    def task_ready_stream(self, worker_id: str) -> str:
        return self._slot(f"{self.TASK_READY_PREFIX}:{worker_id}")

    def task_result_stream(self) -> str:
        return self._ns(self.TASK_RESULT_PREFIX)

    def heartbeat_key(self, worker_id: str) -> str:
        return self._slot(f"{self.WORKER_HEARTBEAT_PREFIX}:{worker_id}")

    def log_stream_key(self, run_id: str) -> str:
        return self._ns(f"{self.LOG_STREAM_PREFIX}:{run_id}")

    def consumer_group_name(self) -> str:
        return self._ns("workers")

    def spider_data_stream(self, run_id: str) -> str:
        return self._slot(f"spider:{run_id}:data")

    def spider_meta_key(self, run_id: str) -> str:
        return self._slot(f"spider:{run_id}:meta")

    def spider_item_ids_key(self, run_id: str) -> str:
        return self._slot(f"spider:{run_id}:item-ids")

    def spider_item_order_key(self, run_id: str) -> str:
        return self._slot(f"spider:{run_id}:item-order")

    def spider_tombstone_key(self, run_id: str) -> str:
        return self._slot(f"spider:{run_id}:tombstone")

    def spider_index_key(self, project_id: str) -> str:
        return self._slot(f"{self.SPIDER_INDEX_PREFIX}:{project_id}")

    def spider_index_expiry_key(self, project_id: str) -> str:
        return self._slot(f"{self.SPIDER_INDEX_PREFIX}:expiry:{project_id}")

    def spider_config_key(self, project_id: str) -> str:
        return self._ns(f"{self.SPIDER_CONFIG_PREFIX}:{project_id}")
