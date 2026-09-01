"""Worker 侧 Redis key 的唯一命名处。

约定：冒号分层、小写加下划线、``{namespace}:{resource_type}:{identifier}``。
需要与其他 key 落在同一 slot 的（跨 key 事务/Lua）额外带 ``{}`` hash tag。
"""

from dataclasses import dataclass
from typing import ClassVar

from antcode_core.infrastructure.redis import (
    control_group as shared_control_group,
)
from antcode_core.infrastructure.redis import (
    control_stream as shared_control_stream,
)
from antcode_core.infrastructure.redis import (
    log_chunk_stream_key as shared_log_chunk_stream_key,
)
from antcode_core.infrastructure.redis import (
    log_ingest_stream_key as shared_log_ingest_stream_key,
)
from antcode_core.infrastructure.redis import (
    log_stream_key as shared_log_stream_key,
)
from antcode_core.infrastructure.redis import (
    redis_namespace,
)
from antcode_core.infrastructure.redis import (
    task_ready_stream as shared_task_ready_stream,
)
from antcode_core.infrastructure.redis import (
    task_result_stream as shared_task_result_stream,
)
from antcode_core.infrastructure.redis import (
    worker_group as shared_worker_group,
)
from antcode_core.infrastructure.redis import (
    worker_heartbeat_key as shared_worker_heartbeat_key,
)


@dataclass(frozen=True)
class RedisKeyConfig:
    namespace: str = redis_namespace()

    stream_max_len: int = 10000
    stream_approx_max_len: bool = True  # 近似裁剪

    heartbeat_ttl: int = 90  # 秒，取 3 倍心跳间隔
    result_ttl: int = 86400  # 秒
    log_ttl: int = 86400  # 秒


class RedisKeys:
    """任务队列、控制通道、日志、心跳、结果与确认流的 key 生成器。"""

    DEFAULT_NAMESPACE: ClassVar[str] = redis_namespace()

    def __init__(self, namespace: str | None = None, config: RedisKeyConfig | None = None):
        self._namespace = (namespace or self.DEFAULT_NAMESPACE).strip() or self.DEFAULT_NAMESPACE
        self._config = config or RedisKeyConfig(namespace=self._namespace)

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def config(self) -> RedisKeyConfig:
        return self._config

    # ==================== 任务队列 Keys ====================

    def task_ready_stream(self, worker_id: str | None = None) -> str:
        """平台向 Worker 分发任务的 Stream；``worker_id`` 为空（构造阶段）时返回同 slot 的占位 key。"""
        if worker_id:
            return shared_task_ready_stream(worker_id, namespace=self._namespace)
        return f"{{{self._namespace}}}:task:ready"

    def task_pending_stream(self, worker_id: str) -> str:
        """已分发未完成的任务，供 XAUTOCLAIM 回收。"""
        return f"{self._namespace}:task:pending:{worker_id}"

    def task_result_stream(self) -> str:
        """Worker 上报任务执行结果。"""
        return shared_task_result_stream(namespace=self._namespace)

    def task_ack_stream(self) -> str:
        """Worker 确认任务接收/拒绝。"""
        return f"{self._namespace}:task:ack"

    # ==================== 控制通道 Keys ====================

    def control_stream(self, worker_id: str) -> str:
        """向 Worker 发送控制命令（取消、kill、配置更新等）。"""
        return shared_control_stream(worker_id, namespace=self._namespace)

    # ==================== 日志 Keys ====================

    def log_stream(self, run_id: str) -> str:
        """实时日志流。"""
        return shared_log_stream_key(run_id, namespace=self._namespace)

    def log_ingest_stream(self) -> str:
        """Worker 直连日志落库 Stream key。"""
        return shared_log_ingest_stream_key(namespace=self._namespace)

    def log_chunk_stream(self, run_id: str) -> str:
        """日志分片传输。"""
        return shared_log_chunk_stream_key(run_id, namespace=self._namespace)

    def log_metadata_key(self, run_id: str) -> str:
        """日志元信息（总行数、最后序号等）。"""
        return f"{self._namespace}:log:meta:{run_id}"

    # ==================== 心跳 Keys ====================

    def heartbeat_key(self, worker_id: str) -> str:
        """Worker 心跳信息。"""
        return shared_worker_heartbeat_key(worker_id, namespace=self._namespace)

    def heartbeat_set(self) -> str:
        """活跃 Worker 的 ID 集合，供快速查询。"""
        return f"{self._namespace}:heartbeat:active"

    # ==================== Worker 注册 Keys ====================

    def worker_info_key(self, worker_id: str) -> str:
        """Worker 静态信息（labels、zone、capabilities 等）。"""
        return f"{self._namespace}:worker:info:{worker_id}"

    def worker_state_key(self, worker_id: str) -> str:
        """Worker 动态状态（running_tasks、queue_depth 等）。"""
        return f"{self._namespace}:worker:state:{worker_id}"

    def worker_set(self) -> str:
        """所有已注册 Worker 的 ID。"""
        return f"{self._namespace}:worker:all"

    # ==================== 消费者组 Keys ====================

    def consumer_group_name(self, purpose: str = "workers") -> str:
        """消费者组名称；``workers`` 与 ``control`` 走 antcode_core 的共享命名。"""
        if purpose == "workers":
            return shared_worker_group(namespace=self._namespace)
        if purpose == "control":
            return shared_control_group(namespace=self._namespace)
        return f"{self._namespace}-{purpose}"

    def consumer_name(self, worker_id: str, instance_id: str | None = None) -> str:
        """消费者名称；``instance_id`` 用于同一 Worker 多实例场景。"""
        if instance_id:
            return f"{worker_id}-{instance_id}"
        return worker_id

    # ==================== 锁 Keys ====================

    def lock_key(self, resource: str, resource_id: str) -> str:
        """分布式锁；``resource`` 为资源类型（如 "task"、"runtime"）。"""
        return f"{self._namespace}:lock:{resource}:{resource_id}"

    def runtime_build_lock(self, runtime_hash: str) -> str:
        """防止同一 runtime_hash 并发构建。"""
        return f"{self._namespace}:lock:runtime:{runtime_hash}"

    # ==================== 指标 Keys ====================

    def metrics_key(self, metric_name: str) -> str:
        """指标 key。"""
        return f"{self._namespace}:metrics:{metric_name}"

    # ==================== 爬虫数据 Keys ====================

    def spider_data_stream(self, run_id: str) -> str:
        """爬虫抓取的数据条目。"""
        return f"{{{self._namespace}}}:spider:{run_id}:data"

    def spider_meta_key(self, run_id: str) -> str:
        """爬虫运行元信息（状态、计数等）。"""
        return f"{{{self._namespace}}}:spider:{run_id}:meta"

    def spider_item_ids_key(self, run_id: str) -> str:
        return f"{{{self._namespace}}}:spider:{run_id}:item-ids"

    def spider_item_order_key(self, run_id: str) -> str:
        return f"{{{self._namespace}}}:spider:{run_id}:item-order"

    def spider_tombstone_key(self, run_id: str) -> str:
        return f"{{{self._namespace}}}:spider:{run_id}:tombstone"

    def spider_index_key(self, project_id: str) -> str:
        """按时间戳索引项目的所有运行记录。"""
        return f"{{{self._namespace}}}:spider:index:{project_id}"

    def spider_index_expiry_key(self, project_id: str) -> str:
        return f"{{{self._namespace}}}:spider:index:expiry:{project_id}"

    def spider_config_key(self, project_id: str) -> str:
        """项目的爬虫配置（schema、去重字段等）。"""
        return f"{self._namespace}:spider:config:{project_id}"

    # ==================== 工具方法 ====================

    @staticmethod
    def parse_key(key: str) -> dict[str, str | list[str]]:
        """按冒号拆出 namespace/type/subtype/id。"""
        parts = key.split(":")
        result: dict[str, str | list[str]] = {"raw": key, "parts": parts}

        if len(parts) >= 1:
            result["namespace"] = parts[0]
        if len(parts) >= 2:
            result["type"] = parts[1]
        if len(parts) >= 3:
            result["subtype"] = parts[2]
        if len(parts) >= 4:
            result["id"] = parts[3]

        return result

    def match_pattern(self, key_type: str, subtype: str | None = None) -> str:
        """SCAN/KEYS 用的匹配模式。"""
        if subtype:
            return f"{self._namespace}:{key_type}:{subtype}:*"
        return f"{self._namespace}:{key_type}:*"


default_keys = RedisKeys()
