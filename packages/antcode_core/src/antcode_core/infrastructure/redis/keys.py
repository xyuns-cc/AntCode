"""Redis Key 命名规范

定义统一的 Redis Key 命名规则，避免 Key 冲突。
"""


class RedisKeys:
    """Redis Key 命名空间"""

    DEFAULT_NAMESPACE = "antcode"

    # === 任务相关 ===
    TASK_QUEUE_PREFIX = "task:queue"
    TASK_READY_PREFIX = "task:ready"
    TASK_DELAY_PREFIX = "task:delay"
    TASK_RUNNING_PREFIX = "task:running"
    TASK_RESULT_PREFIX = "task:result"

    # === 日志相关 ===
    LOG_STREAM_PREFIX = "log:stream"
    LOG_ARCHIVE_PREFIX = "log:archive"

    # === Worker 相关 ===
    WORKER_STATUS_PREFIX = "worker:status"
    WORKER_HEARTBEAT_PREFIX = "worker:heartbeat"
    WORKER_SLOTS_PREFIX = "worker:slots"

    # === Spider 相关 ===
    SPIDER_DATA_PREFIX = "spider:data"
    SPIDER_META_PREFIX = "spider:meta"
    SPIDER_INDEX_PREFIX = "spider:index"
    SPIDER_CONFIG_PREFIX = "spider:config"

    # === 监控相关 ===
    MONITOR_STATUS_PREFIX = "monitor:worker"
    MONITOR_STREAM_KEY = "monitor:stream:metrics"
    MONITOR_CLUSTER_SET = "monitor:cluster:workers"

    # === 分布式锁 ===
    LOCK_PREFIX = "lock"
    LEADER_LOCK_KEY = "lock:leader:master"

    # === 缓存相关 ===
    CACHE_PREFIX = "cache"

    # === Bloom Filter ===
    BLOOM_PREFIX = "bloom"

    def __init__(self, namespace: str | None = None) -> None:
        self.namespace = namespace or self.DEFAULT_NAMESPACE

    def _ns(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    # === 兼容旧用法（实例方法） ===
    def task_ready_stream(self, worker_id: str) -> str:
        return self._ns(f"{self.TASK_READY_PREFIX}:{worker_id}")

    def task_result_stream(self) -> str:
        return self._ns(self.TASK_RESULT_PREFIX)

    def heartbeat_key(self, worker_id: str) -> str:
        return self._ns(f"{self.WORKER_HEARTBEAT_PREFIX}:{worker_id}")

    def log_stream_key(self, run_id: str) -> str:
        return self._ns(f"{self.LOG_STREAM_PREFIX}:{run_id}")

    def consumer_group_name(self) -> str:
        return self._ns("workers")

    def spider_data_stream(self, run_id: str) -> str:
        return self._ns(f"{self.SPIDER_DATA_PREFIX}:{run_id}")

    def spider_meta_key(self, run_id: str) -> str:
        return self._ns(f"{self.SPIDER_META_PREFIX}:{run_id}")

    def spider_index_key(self, project_id: str) -> str:
        return self._ns(f"{self.SPIDER_INDEX_PREFIX}:{project_id}")

    def spider_config_key(self, project_id: str) -> str:
        return self._ns(f"{self.SPIDER_CONFIG_PREFIX}:{project_id}")

    @classmethod
    def task_queue(cls, priority: int = 0) -> str:
        """任务队列 Key"""
        return f"{cls.TASK_QUEUE_PREFIX}:{priority}"

    @classmethod
    def task_ready(cls, priority: int = 0) -> str:
        """就绪队列 Key（Redis Stream）"""
        return f"{cls.TASK_READY_PREFIX}:{priority}"

    @classmethod
    def task_delay(cls) -> str:
        """延迟队列 Key（Redis ZSet）"""
        return cls.TASK_DELAY_PREFIX

    @classmethod
    def task_running(cls, worker_id: str) -> str:
        """运行中任务 Key"""
        return f"{cls.TASK_RUNNING_PREFIX}:{worker_id}"

    @classmethod
    def task_result(cls, run_id: str) -> str:
        """任务结果 Key"""
        return f"{cls.TASK_RESULT_PREFIX}:{run_id}"

    @classmethod
    def log_stream(cls, run_id: str) -> str:
        """日志流 Key（Redis Stream）"""
        return f"{cls.LOG_STREAM_PREFIX}:{run_id}"

    @classmethod
    def log_archive(cls, run_id: str) -> str:
        """日志归档路径 Key"""
        return f"{cls.LOG_ARCHIVE_PREFIX}:{run_id}"

    @classmethod
    def worker_status(cls, worker_id: str) -> str:
        """Worker 状态 Key"""
        return f"{cls.WORKER_STATUS_PREFIX}:{worker_id}"

    @classmethod
    def worker_heartbeat(cls, worker_id: str) -> str:
        """Worker 心跳 Key"""
        return f"{cls.WORKER_HEARTBEAT_PREFIX}:{worker_id}"

    @classmethod
    def worker_slots(cls, worker_id: str) -> str:
        """Worker 槽位 Key"""
        return f"{cls.WORKER_SLOTS_PREFIX}:{worker_id}"

    @classmethod
    def monitor_status(cls, worker_id: str) -> str:
        """监控状态 Key"""
        return f"{cls.MONITOR_STATUS_PREFIX}:{worker_id}:status"

    @classmethod
    def monitor_history(cls, worker_id: str) -> str:
        """监控历史 Key"""
        return f"{cls.MONITOR_STATUS_PREFIX}:{worker_id}:history"

    @classmethod
    def lock(cls, name: str) -> str:
        """分布式锁 Key"""
        return f"{cls.LOCK_PREFIX}:{name}"

    @classmethod
    def cache(cls, namespace: str, key: str) -> str:
        """缓存 Key"""
        return f"{cls.CACHE_PREFIX}:{namespace}:{key}"

    @classmethod
    def bloom(cls, name: str) -> str:
        """Bloom Filter Key"""
        return f"{cls.BLOOM_PREFIX}:{name}"
