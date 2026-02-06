"""
任务轮询处理器

代理 Worker poll 任务，从 Redis Streams ready queues 读取任务。

**Validates: Requirements 6.5**
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger


@dataclass
class TaskInfo:
    """任务信息"""

    task_id: str
    project_id: str
    run_id: str = ""
    project_type: str = "spider"
    priority: int = 0
    timeout: int = 3600
    download_url: str = ""
    file_hash: str = ""
    entry_point: str = ""
    params: dict[str, object] = field(default_factory=dict)
    environment: dict[str, object] = field(default_factory=dict)
    receipt_id: str = ""


class TaskPollHandler:
    """任务轮询处理器

    代理 Worker 从 Redis Streams ready queues 读取任务。
    Gateway 不实现调度策略，只负责代理队列读取。
    """

    # Redis Streams 键前缀
    READY_QUEUE_PREFIX = "antcode:task:ready:"
    WORKER_GROUP = "antcode-workers"

    def __init__(self, redis_client=None):
        """初始化处理器

        Args:
            redis_client: Redis 客户端，默认延迟初始化
        """
        self._redis_client = redis_client

    async def _get_redis_client(self):
        """获取 Redis 客户端"""
        if self._redis_client is None:
            try:
                from antcode_core.infrastructure.redis import get_redis_client

                self._redis_client = await get_redis_client()
            except ImportError:
                logger.warning("antcode_core.infrastructure.redis 不可用")
                return None
        return self._redis_client

    async def handle(
        self,
        worker_id: str,
        max_tasks: int = 1,
        block_ms: int = 5000,
        queues: list[str] | None = None,
    ) -> list[TaskInfo]:
        """处理任务轮询请求

        从 Redis Streams ready queues 读取任务。

        Args:
            worker_id: Worker ID
            max_tasks: 最多返回的任务数
            block_ms: 阻塞等待时间（毫秒）
            queues: 要读取的队列列表，默认读取所有队列

        Returns:
            任务列表
        """
        logger.debug(
            f"Worker {worker_id} 轮询任务，最多 {max_tasks} 个，"
            f"阻塞 {block_ms}ms"
        )

        redis = await self._get_redis_client()
        if redis is None:
            logger.warning("Redis 不可用，返回空任务列表")
            return []

        try:
            # 确定要读取的队列
            if queues is None:
                # 默认读取 worker 专属队列
                queues = [f"{self.READY_QUEUE_PREFIX}{worker_id}"]

            # 确保消费者组存在
            for queue in queues:
                try:
                    await redis.xgroup_create(queue, self.WORKER_GROUP, id="0", mkstream=True)
                except Exception as e:
                    if "BUSYGROUP" not in str(e):
                        raise

            # 构建 streams 参数
            streams = dict.fromkeys(queues, ">")

            # 从 Redis Streams 读取任务
            # 使用 XREADGROUP 确保消息只被一个消费者处理
            results = await redis.xreadgroup(
                groupname=self.WORKER_GROUP,
                consumername=worker_id,
                streams=streams,
                count=max_tasks,
                block=block_ms,
            )

            if not results:
                return []

            tasks = []
            for stream_name, messages in results:
                for message_id, data in messages:
                    task = self._parse_task_data(data, message_id)
                    if task:
                        task.receipt_id = f"{stream_name}|{message_id}"
                        tasks.append(task)
                        logger.debug(
                            f"读取任务: task_id={task.task_id}, "
                            f"stream={stream_name}, message_id={message_id}"
                        )

            logger.info(
                f"Worker {worker_id} 获取了 {len(tasks)} 个任务"
            )
            return tasks

        except Exception as e:
            logger.error(f"读取任务失败: {e}")
            return []

    def _parse_task_data(
        self,
        data: dict,
        message_id: str,
    ) -> TaskInfo | None:
        """解析任务数据

        Args:
            data: Redis Stream 消息数据
            message_id: 消息 ID

        Returns:
            任务信息，解析失败返回 None
        """
        try:
            # 解码字节数据
            decoded = {}
            for k, v in data.items():
                key = k.decode() if isinstance(k, bytes) else k
                value = v.decode() if isinstance(v, bytes) else v
                decoded[key] = value

            task_id = decoded.get("task_id")
            if not task_id:
                logger.warning(f"任务数据缺少 task_id: {message_id}")
                return None

            return TaskInfo(
                task_id=task_id,
                project_id=decoded.get("project_id", ""),
                run_id=decoded.get("run_id", decoded.get("execution_id", "")),
                project_type=decoded.get("project_type", "spider"),
                priority=int(decoded.get("priority", 0)),
                timeout=int(decoded.get("timeout", 3600)),
                download_url=decoded.get("download_url", ""),
                file_hash=decoded.get("file_hash", ""),
                entry_point=decoded.get("entry_point", ""),
                params=self._parse_json(decoded.get("params", "{}")),
                environment=self._parse_json(decoded.get("environment", "{}")),
            )

        except Exception as e:
            logger.error(f"解析任务数据失败: {e}, message_id={message_id}")
            return None

    def _parse_json(self, value: str) -> dict[str, object]:
        """解析 JSON 字符串"""
        if not value:
            return {}
        try:
            import json

            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    async def ack_task(
        self,
        worker_id: str,
        queue: str,
        message_id: str,
    ) -> bool:
        """确认任务已处理

        Args:
            worker_id: Worker ID
            queue: 队列名称
            message_id: 消息 ID

        Returns:
            是否成功
        """
        redis = await self._get_redis_client()
        if redis is None:
            return False

        try:
            await redis.xack(queue, self.WORKER_GROUP, message_id)
            logger.debug(
                f"任务已确认: worker_id={worker_id}, "
                f"queue={queue}, message_id={message_id}"
            )
            return True
        except Exception as e:
            logger.error(f"确认任务失败: {e}")
            return False

    async def ack_receipt(
        self,
        receipt_id: str,
        accepted: bool = True,
        reason: str = "",
    ) -> bool:
        """确认任务（receipt 形式）"""
        if "|" not in receipt_id:
            return False
        queue, message_id = receipt_id.split("|", 1)
        if accepted:
            return await self.ack_task("", queue, message_id)
        return await self._requeue_task(queue, message_id, reason)

    async def _requeue_task(self, queue: str, message_id: str, reason: str) -> bool:
        """拒绝任务并重新入队"""
        redis = await self._get_redis_client()
        if redis is None:
            return False

        try:
            messages = await redis.xrange(queue, min=message_id, max=message_id, count=1)
            if not messages:
                return False

            _, data = messages[0]
            decoded = {
                (k.decode() if isinstance(k, bytes) else k): (
                    v.decode() if isinstance(v, bytes) else v
                )
                for k, v in data.items()
            }
            decoded["requeue_reason"] = reason
            decoded["requeue_at"] = datetime.now().isoformat()

            await redis.xadd(queue, decoded)
            await redis.xack(queue, self.WORKER_GROUP, message_id)
            return True
        except Exception as e:
            logger.error(f"重新入队失败: {e}")
            return False
