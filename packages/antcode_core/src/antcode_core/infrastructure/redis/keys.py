"""Spider 数据面与运行日志的 Redis Key 命名。

任务分发 / 心跳 / 消费组的 key 不归本类：权威定义是 ``control_plane``
的模块级函数，Master、Gateway、Worker 全走它。本类曾有一份同名同签名的
副本，且 ``heartbeat_key``（``{ns}:worker:heartbeat:*`` vs 权威的
``{ns}:heartbeat:*``）与 ``consumer_group_name``（``ns:workers`` vs
``ns-workers``）生成的是没人读写的 key，已删——不要再往回加。
"""


class RedisKeys:
    """Redis Key 命名空间"""

    DEFAULT_NAMESPACE = "antcode"

    # === 日志相关 ===
    LOG_STREAM_PREFIX = "log:stream"

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
    def log_stream_key(self, run_id: str) -> str:
        return self._ns(f"{self.LOG_STREAM_PREFIX}:{run_id}")

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
