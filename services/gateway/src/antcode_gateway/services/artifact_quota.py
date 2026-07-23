"""Per-run artifact upload quota for the Gateway (P1-SEC-04).

上限取值与 worker 端 ``artifact_collector`` 的
``DEFAULT_MAX_FILE_COUNT`` / ``DEFAULT_MAX_TOTAL_SIZE`` 对齐：受信 worker
本就不会超过这些上限，服务端强制执行只拦截被攻破/伪造的 worker，
不会锁死任何合法路径。

实现说明（如实记录的边界）：artifact 存储是内容寻址的去重 blob 库，
没有 run 维度的持久记录；而上传发生在 run 结算之前，DB 中的
``TaskRun.result_data['artifacts']`` 在上传时刻还不存在。因此配额以
Gateway 进程内计数实现：

- 单进程内可严格阻止单 run 无限占用存储；
- 残余风险：Gateway 重启或多副本部署下每个进程独立计数，攻击者最多
  把配额放大到 N 倍（N = 重启次数/副本数），但单文件 100MiB 上限、
  run ownership + 终态闸门仍然生效，无法无限增长。

P1-round6 5.3 (observability): 暴露 ``metrics()`` 只读快照,
``{tracked_runs, evicted_runs, exceeded_events}``,让运维/告警观察
LRU 驱逐率(说明 max_tracked_runs 太小)与配额触发频次(说明合法
worker 请求接近上限或有异常客户端)。数据仅进程内,重启会重置——与
quota 本身语义一致。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from loguru import logger

MEBIBYTE = 1024 * 1024
# 与 antcode_worker.executor.artifact_collector.DEFAULT_MAX_FILE_COUNT 对齐
MAX_ARTIFACTS_PER_RUN = 1000
# 与 antcode_worker.executor.artifact_collector.DEFAULT_MAX_TOTAL_SIZE 对齐
MAX_ARTIFACT_BYTES_PER_RUN = 500 * MEBIBYTE
# 进程内跟踪的 run 数上限（LRU 驱逐），限制配额账本自身的内存占用。
# 上传前已通过 run ownership + 活跃 run 校验，能进入账本的 run 有限。
MAX_TRACKED_RUNS = 4096

# P1-round6 5.3: Redis 备份 key 模板 (Cluster hash tag = run_id 让 count/bytes
# 落同 slot, 支持原子 HSET/HINCRBY)。TTL 与合理 run 生命周期对齐 (24h)。
_QUOTA_KEY_TEMPLATE = "{{{ns}}}:{{{run_id}}}:artifact:quota"
_QUOTA_TTL_SECONDS = 24 * 3600


class RunArtifactQuotaExceeded(Exception):
    """单 run 的 artifact 数量或总字节配额已耗尽。"""


@dataclass
class _RunUploadUsage:
    """单 run 已预留的上传用量。"""

    count: int = 0
    total_bytes: int = 0


class RunArtifactQuota:
    """Gateway 进程内的 per-run artifact 上传配额账本。

    ``reserve`` 在接收上传前按声明大小占额（超限抛
    ``RunArtifactQuotaExceeded``），上传失败时用 ``release`` 归还。
    声明大小与实际大小的一致性由上传证据校验（sha256 + size）保证，
    因此预留量即最终占用量。
    """

    def __init__(
        self,
        *,
        max_artifacts: int = MAX_ARTIFACTS_PER_RUN,
        max_total_bytes: int = MAX_ARTIFACT_BYTES_PER_RUN,
        max_tracked_runs: int = MAX_TRACKED_RUNS,
        redis_client: Any = None,
        namespace: str = "antcode",
    ) -> None:
        if max_artifacts < 1 or max_total_bytes < 1 or max_tracked_runs < 1:
            raise ValueError("artifact 配额参数必须为正数")
        self._max_artifacts = max_artifacts
        self._max_total_bytes = max_total_bytes
        self._max_tracked_runs = max_tracked_runs
        self._usage: OrderedDict[str, _RunUploadUsage] = OrderedDict()
        # P1-round6 5.3 (observability): 累积计数, 便于运维观察配额边界
        self._evicted_runs = 0
        self._exceeded_events = 0
        # P1-round6 5.3 (durability): 可选 Redis 备份, 进程重启后能 restore
        # 已占用的 count/bytes, 攻击者无法通过 Gateway 重启把 quota 放大到
        # N 倍。None 时降级纯内存(向后兼容 + 单元测试)。
        self._redis = redis_client
        self._namespace = namespace

    def reserve(self, run_id: str, size_bytes: int) -> None:
        """为一次上传预留配额；超限抛 ``RunArtifactQuotaExceeded``。"""
        if size_bytes < 0:
            raise ValueError("artifact size_bytes 不能为负数")
        usage = self._touch(run_id)
        if usage.count + 1 > self._max_artifacts:
            self._exceeded_events += 1
            raise RunArtifactQuotaExceeded(f"run {run_id} artifact 数量超过上限 {self._max_artifacts}")
        if usage.total_bytes + size_bytes > self._max_total_bytes:
            self._exceeded_events += 1
            raise RunArtifactQuotaExceeded(f"run {run_id} artifact 总字节超过上限 {self._max_total_bytes}")
        usage.count += 1
        usage.total_bytes += size_bytes

    def release(self, run_id: str, size_bytes: int) -> None:
        """上传失败时归还此前 ``reserve`` 的配额。"""
        usage = self._usage.get(run_id)
        if usage is None:
            return
        usage.count = max(0, usage.count - 1)
        usage.total_bytes = max(0, usage.total_bytes - size_bytes)

    def usage_of(self, run_id: str) -> tuple[int, int]:
        """返回 (已占数量, 已占字节)，仅供测试与观测。"""
        usage = self._usage.get(run_id)
        if usage is None:
            return (0, 0)
        return (usage.count, usage.total_bytes)

    def _touch(self, run_id: str) -> _RunUploadUsage:
        """取出（或创建）run 账目并置为最近使用；超容量时驱逐最旧条目。"""
        usage = self._usage.get(run_id)
        if usage is None:
            usage = _RunUploadUsage()
            self._usage[run_id] = usage
        else:
            self._usage.move_to_end(run_id)
        while len(self._usage) > self._max_tracked_runs:
            self._usage.popitem(last=False)
            self._evicted_runs += 1
        return usage

    def _redis_key(self, run_id: str) -> str:
        return _QUOTA_KEY_TEMPLATE.format(ns=self._namespace, run_id=run_id)

    async def restore_from_redis(self, run_id: str) -> None:
        """P1-round6 5.3: Gateway 重启后按需从 Redis 恢复 run 的用量账目。

        应在 reserve 前调用(如果内存 miss); 无 redis_client / 无键 / Redis
        不可达时 no-op, 让 quota 走纯内存兜底(仍能 fence 后续超限)。
        HGETALL 返回 bytes → int, 空表返回 0。
        """
        if self._redis is None or run_id in self._usage:
            return
        try:
            data = await self._redis.hgetall(self._redis_key(run_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("artifact quota restore Redis 查询失败: run={} err={}", run_id, exc)
            return
        if not data:
            return

        def _int(v: Any) -> int:
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace")
            try:
                return max(0, int(v))
            except (TypeError, ValueError):
                return 0

        # Redis-py 有的返回 str-key 有的返回 bytes-key, 都归一
        norm = {(k.decode() if isinstance(k, bytes) else k): v for k, v in data.items()}
        usage = _RunUploadUsage(count=_int(norm.get("count")), total_bytes=_int(norm.get("total_bytes")))
        self._usage[run_id] = usage
        # 触发 LRU 驱逐边界
        while len(self._usage) > self._max_tracked_runs:
            self._usage.popitem(last=False)
            self._evicted_runs += 1

    async def async_reserve(self, run_id: str, size_bytes: int) -> None:
        """P1-round6 5.3: 在 reserve 前先尝试从 Redis 恢复 (幂等),然后 fence 计数
        + 写回 Redis 备份。async 变体, 调用方在 grpc handler 中 await。
        Redis 写失败仅 warn, 不阻塞 quota 判定(内存已 fence)。
        """
        await self.restore_from_redis(run_id)
        self.reserve(run_id, size_bytes)
        if self._redis is None:
            return
        try:
            key = self._redis_key(run_id)
            pipeline = self._redis.pipeline(transaction=False)
            pipeline.hincrby(key, "count", 1)
            pipeline.hincrby(key, "total_bytes", size_bytes)
            pipeline.expire(key, _QUOTA_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("artifact quota Redis 备份写入失败(仅内存 fence): run={} err={}", run_id, exc)

    async def async_release(self, run_id: str, size_bytes: int) -> None:
        """与 async_reserve 对称的归还; Redis 失败仅 warn。"""
        self.release(run_id, size_bytes)
        if self._redis is None:
            return
        try:
            key = self._redis_key(run_id)
            pipeline = self._redis.pipeline(transaction=False)
            pipeline.hincrby(key, "count", -1)
            pipeline.hincrby(key, "total_bytes", -size_bytes)
            await pipeline.execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("artifact quota Redis 备份归还失败: run={} err={}", run_id, exc)

    def metrics(self) -> dict[str, int]:
        """P1-round6 5.3: 只读快照, 供 /metrics 或健康检查暴露给运维观察。

        - tracked_runs: 当前账本 run 数(接近 max_tracked_runs 说明该调大)
        - evicted_runs: 累计 LRU 驱逐次数(非零说明 tracked 上限过小,
          攻击者可能通过 run 换代绕过配额)
        - exceeded_events: 累计触发 RunArtifactQuotaExceeded 的次数
        """
        return {
            "tracked_runs": len(self._usage),
            "evicted_runs": self._evicted_runs,
            "exceeded_events": self._exceeded_events,
        }


__all__ = [
    "MAX_ARTIFACTS_PER_RUN",
    "MAX_ARTIFACT_BYTES_PER_RUN",
    "MAX_TRACKED_RUNS",
    "RunArtifactQuota",
    "RunArtifactQuotaExceeded",
]
