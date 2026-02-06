"""
Redis Key 命名规范模块

定义 Worker 与 Redis 交互时使用的所有 key 命名规范。
确保 key 命名一致性，便于维护和调试。

Key 命名约定：
- 使用冒号 `:` 作为层级分隔符
- 使用小写字母和下划线
- 格式：{namespace}:{resource_type}:{identifier}

Requirements: 5.3
"""

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class RedisKeyConfig:
    """Redis Key 配置"""

    # 命名空间前缀
    namespace: str = "antcode"

    # Stream 相关配置
    stream_max_len: int = 10000  # Stream 最大长度
    stream_approx_max_len: bool = True  # 使用近似裁剪

    # 过期时间配置（秒）
    heartbeat_ttl: int = 90  # 心跳 key 过期时间（3 倍心跳间隔）
    result_ttl: int = 86400  # 结果 key 过期时间（24 小时）
    log_ttl: int = 86400  # 日志 key 过期时间（24 小时）


class RedisKeys:
    """
    Redis Key 生成器

    提供统一的 key 命名规范，支持：
    - 任务队列（ready queue）
    - 控制通道（control channel）
    - 日志流（log stream）
    - 心跳（heartbeat）
    - 结果流（result stream）
    - 确认流（ack stream）

    Requirements: 5.3
    """

    # 默认命名空间
    DEFAULT_NAMESPACE: ClassVar[str] = "antcode"

    def __init__(self, namespace: str | None = None, config: RedisKeyConfig | None = None):
        """
        初始化 Key 生成器

        Args:
            namespace: 命名空间前缀，默认为 "antcode"
            config: Key 配置
        """
        self._namespace = namespace or self.DEFAULT_NAMESPACE
        self._config = config or RedisKeyConfig(namespace=self._namespace)

    @property
    def namespace(self) -> str:
        """获取命名空间"""
        return self._namespace

    @property
    def config(self) -> RedisKeyConfig:
        """获取配置"""
        return self._config

    # ==================== 任务队列 Keys ====================

    def task_ready_stream(self, worker_id: str | None = None) -> str:
        """
        任务就绪队列 Stream key

        用于平台向 Worker 分发任务。
        如果指定 worker_id，则为该 Worker 专属队列。

        Args:
            worker_id: Worker ID，为 None 时返回全局队列

        Returns:
            Stream key，如 "antcode:task:ready" 或 "antcode:task:ready:{worker_id}"
        """
        if worker_id:
            return f"{self._namespace}:task:ready:{worker_id}"
        return f"{self._namespace}:task:ready"

    def task_pending_stream(self, worker_id: str) -> str:
        """
        任务 pending 队列 Stream key

        用于跟踪已分发但未完成的任务（用于 XAUTOCLAIM 回收）。

        Args:
            worker_id: Worker ID

        Returns:
            Stream key，如 "antcode:task:pending:{worker_id}"
        """
        return f"{self._namespace}:task:pending:{worker_id}"

    def task_result_stream(self) -> str:
        """
        任务结果 Stream key

        Worker 上报任务执行结果。

        Returns:
            Stream key，如 "antcode:task:result"
        """
        return f"{self._namespace}:task:result"

    def task_ack_stream(self) -> str:
        """
        任务确认 Stream key

        Worker 确认任务接收/拒绝。

        Returns:
            Stream key，如 "antcode:task:ack"
        """
        return f"{self._namespace}:task:ack"

    # ==================== 控制通道 Keys ====================

    def control_stream(self, worker_id: str) -> str:
        """
        控制通道 Stream key

        用于向 Worker 发送控制命令（取消、kill、配置更新等）。

        Args:
            worker_id: Worker ID

        Returns:
            Stream key，如 "antcode:control:{worker_id}"
        """
        return f"{self._namespace}:control:{worker_id}"

    def control_global_stream(self) -> str:
        """
        全局控制通道 Stream key

        用于向所有 Worker 广播控制命令。

        Returns:
            Stream key，如 "antcode:control:global"
        """
        return f"{self._namespace}:control:global"

    # ==================== 日志 Keys ====================

    def log_stream(self, run_id: str) -> str:
        """
        日志 Stream key

        用于实时日志流。

        Args:
            run_id: 运行 ID

        Returns:
            Stream key，如 "antcode:log:stream:{run_id}"
        """
        return f"{self._namespace}:log:stream:{run_id}"

    def log_chunk_stream(self, run_id: str) -> str:
        """
        日志分片 Stream key

        用于日志分片传输。

        Args:
            run_id: 运行 ID

        Returns:
            Stream key，如 "antcode:log:chunk:{run_id}"
        """
        return f"{self._namespace}:log:chunk:{run_id}"

    def log_metadata_key(self, run_id: str) -> str:
        """
        日志元数据 Hash key

        存储日志的元信息（总行数、最后序号等）。

        Args:
            run_id: 运行 ID

        Returns:
            Hash key，如 "antcode:log:meta:{run_id}"
        """
        return f"{self._namespace}:log:meta:{run_id}"

    # ==================== 心跳 Keys ====================

    def heartbeat_key(self, worker_id: str) -> str:
        """
        心跳 Hash key

        存储 Worker 心跳信息。

        Args:
            worker_id: Worker ID

        Returns:
            Hash key，如 "antcode:heartbeat:{worker_id}"
        """
        return f"{self._namespace}:heartbeat:{worker_id}"

    def heartbeat_set(self) -> str:
        """
        活跃 Worker 集合 key

        存储所有活跃 Worker 的 ID（用于快速查询）。

        Returns:
            Set key，如 "antcode:heartbeat:active"
        """
        return f"{self._namespace}:heartbeat:active"

    # ==================== Worker 注册 Keys ====================

    def worker_info_key(self, worker_id: str) -> str:
        """
        Worker 信息 Hash key

        存储 Worker 静态信息（labels、zone、capabilities 等）。

        Args:
            worker_id: Worker ID

        Returns:
            Hash key，如 "antcode:worker:info:{worker_id}"
        """
        return f"{self._namespace}:worker:info:{worker_id}"

    def worker_state_key(self, worker_id: str) -> str:
        """
        Worker 状态 Hash key

        存储 Worker 动态状态（running_tasks、queue_depth 等）。

        Args:
            worker_id: Worker ID

        Returns:
            Hash key，如 "antcode:worker:state:{worker_id}"
        """
        return f"{self._namespace}:worker:state:{worker_id}"

    def worker_set(self) -> str:
        """
        所有 Worker 集合 key

        存储所有已注册 Worker 的 ID。

        Returns:
            Set key，如 "antcode:worker:all"
        """
        return f"{self._namespace}:worker:all"

    # ==================== 消费者组 Keys ====================

    def consumer_group_name(self, purpose: str = "workers") -> str:
        """
        消费者组名称

        Args:
            purpose: 用途标识，如 "workers"、"monitors"

        Returns:
            消费者组名称，如 "antcode-workers"
        """
        return f"{self._namespace}-{purpose}"

    def consumer_name(self, worker_id: str, instance_id: str | None = None) -> str:
        """
        消费者名称

        Args:
            worker_id: Worker ID
            instance_id: 实例 ID（用于同一 Worker 多实例场景）

        Returns:
            消费者名称，如 "worker-001" 或 "worker-001-instance-1"
        """
        if instance_id:
            return f"{worker_id}-{instance_id}"
        return worker_id

    # ==================== 锁 Keys ====================

    def lock_key(self, resource: str, resource_id: str) -> str:
        """
        分布式锁 key

        Args:
            resource: 资源类型，如 "task"、"runtime"
            resource_id: 资源 ID

        Returns:
            Lock key，如 "antcode:lock:task:{task_id}"
        """
        return f"{self._namespace}:lock:{resource}:{resource_id}"

    def runtime_build_lock(self, runtime_hash: str) -> str:
        """
        Runtime 构建锁 key

        防止同一 runtime_hash 并发构建。

        Args:
            runtime_hash: Runtime 哈希值

        Returns:
            Lock key，如 "antcode:lock:runtime:{runtime_hash}"
        """
        return f"{self._namespace}:lock:runtime:{runtime_hash}"

    # ==================== 指标 Keys ====================

    def metrics_key(self, metric_name: str) -> str:
        """
        指标 key

        Args:
            metric_name: 指标名称

        Returns:
            Key，如 "antcode:metrics:{metric_name}"
        """
        return f"{self._namespace}:metrics:{metric_name}"

    # ==================== 爬虫数据 Keys ====================

    def spider_data_stream(self, run_id: str) -> str:
        """
        爬虫数据 Stream key

        存储爬虫抓取的数据条目。

        Args:
            run_id: 运行 ID

        Returns:
            Stream key，如 "antcode:spider:data:{run_id}"
        """
        return f"{self._namespace}:spider:data:{run_id}"

    def spider_meta_key(self, run_id: str) -> str:
        """
        爬虫元数据 Hash key

        存储爬虫运行的元信息（状态、计数等）。

        Args:
            run_id: 运行 ID

        Returns:
            Hash key，如 "antcode:spider:meta:{run_id}"
        """
        return f"{self._namespace}:spider:meta:{run_id}"

    def spider_index_key(self, project_id: str) -> str:
        """
        爬虫运行索引 Sorted Set key

        按时间戳索引项目的所有运行记录。

        Args:
            project_id: 项目 ID

        Returns:
            Sorted Set key，如 "antcode:spider:index:{project_id}"
        """
        return f"{self._namespace}:spider:index:{project_id}"

    def spider_config_key(self, project_id: str) -> str:
        """
        爬虫动态配置 Hash key

        存储项目的爬虫配置（schema、去重字段等）。

        Args:
            project_id: 项目 ID

        Returns:
            Hash key，如 "antcode:spider:config:{project_id}"
        """
        return f"{self._namespace}:spider:config:{project_id}"

    # ==================== 工具方法 ====================

    @staticmethod
    def parse_key(key: str) -> dict[str, str]:
        """
        解析 key 结构

        Args:
            key: Redis key

        Returns:
            解析后的字典，包含 namespace、type、id 等
        """
        parts = key.split(":")
        result = {"raw": key, "parts": parts}

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
        """
        生成 key 匹配模式（用于 SCAN/KEYS）

        Args:
            key_type: key 类型，如 "task"、"log"、"heartbeat"
            subtype: 子类型，如 "ready"、"stream"

        Returns:
            匹配模式，如 "antcode:task:*" 或 "antcode:log:stream:*"
        """
        if subtype:
            return f"{self._namespace}:{key_type}:{subtype}:*"
        return f"{self._namespace}:{key_type}:*"


# 默认实例，便于直接使用
default_keys = RedisKeys()
