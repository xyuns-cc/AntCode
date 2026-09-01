"""把一批**已经绑定完成**的任务写进目标 Worker 的 ready stream，写完按已 ACK 游标裁剪。

从 ``worker_dispatcher`` 拆出来，是因为它的失效模式与"挑不出节点"正好相反：这里坏掉的
时候节点已经选好、run 也已经绑上，坏的是我们自己这一侧（Redis 连不上、Master 代际栅栏
不成立、XADD 响应丢失需要查重）。调用方对这两类失败该做的事完全不同——一个是稍后重试，
一个是我们得去修——所以它们不该共用一个模块，更不该共用一条错误路径。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from antcode_core.application.services import lease_fenced_ready_publish as ready_publish
from antcode_core.application.services.lease_capability_snapshot import LeaseCapabilitySnapshot
from antcode_core.infrastructure.redis import task_ready_stream
from antcode_core.observability.tracing import get_current_trace

DEFAULT_TASK_TIMEOUT_SECONDS = 3600

# U3 / #16: ready stream 上限,防止 Worker 长时间挂掉时 stream 无限增长。
# 用 ~10k entries(近似裁剪)。
#
# P1-19: 之前 XADD MAXLEN=N 会物理删除超出的历史 entry,与 consumer
# group ACK 游标无关——Worker 离线积压超过 MAXLEN 时,未 ACK 的老任务
# 会被静默删除。现改为:
#   1) XADD 不带 MAXLEN(避免撞未 ACK)
#   2) XADD 后 XPENDING 拿 group 最小未 ACK msg_id,XTRIM MINID
#      仅裁剪比它老的(即已全部 ACK 的)entry
#   3) 若 PEL 为空,退化为 MAXLEN(全量已消费,安全)
#   4) 未 ACK 数超 ALERT_THRESHOLD 立即告警,提示 Worker 长期离线/卡死
READY_STREAM_MAXLEN = 10000
READY_STREAM_PENDING_ALERT_THRESHOLD = 8000


def _ready_stream_message(task: dict, trace_parent: str) -> dict[str, Any]:
    task_id = task.get("task_id", "")
    return {
        "task_id": task_id,
        # P1-FN-02: 确定性 dispatch_id(=run_id)。XADD 用自动 Stream ID,
        # 响应丢失后靠该字段在 stream 尾部查重确认是否已提交。
        "dispatch_id": task.get("run_id") or task_id,
        "run_id": task.get("run_id") or task_id,
        "project_id": task.get("project_id", ""),
        "project_type": task.get("project_type", "code"),
        "priority": task.get("priority") or 0,
        "params": task.get("params") or {},
        "environment": task.get("environment") or {},
        "runtime_env_name": task.get("runtime_env_name") or "",
        "timeout": task.get("timeout", DEFAULT_TASK_TIMEOUT_SECONDS),
        # A2: source_bundle 契约（direct 模式 poll 侧读同名字段解出 SourceBundle）
        "source_bundle_uri": task.get("source_bundle_uri") or "",
        "source_bundle_sha256": task.get("source_bundle_sha256") or "",
        "source_bundle_size": task.get("source_bundle_size") or 0,
        "transfer_method": task.get("transfer_method") or "source_bundle",
        "source_subdir": task.get("source_subdir") or "",
        "resolved_revision": task.get("resolved_revision") or "",
        "entry_point": task.get("entry_point") or "",
        "trace_parent": trace_parent,
    }


async def _ensure_consumer_group(stream: Any, stream_key: str, group_name: str) -> None:
    """U3: 写入前确保 consumer group 存在,start_id="0" 让起得晚的 Worker 也能读到历史消息。"""
    try:
        await stream.xgroup_create(stream_key, group_name=group_name, start_id="0", mkstream=True)
    except Exception as exc:
        # 非 BUSYGROUP 类异常(网络抖动等)记一行 warning,不阻塞 XADD
        # —— xadd 自带 reconnect,group 即使本次没建上,Worker 端 start
        # 时还会 ensure_consumer_group。
        logger.warning(f"ensure_group 失败,继续 XADD: {exc}")


async def publish_ready_batch_to_worker(
    *,
    worker: Any,
    tasks: list[dict],
    batch_id: str,
    lease_snapshot: LeaseCapabilitySnapshot,
) -> dict[str, Any]:
    """Fence the selected Lease generation and publish one ready batch."""
    # E5: consumer group 名带 namespace 前缀，与 worker 侧对齐
    from antcode_core.infrastructure.redis.control_plane import worker_consumer_group
    from antcode_core.infrastructure.redis.stream_client import StreamClient

    stream = StreamClient()
    stream_key = task_ready_stream(worker.public_id)
    group_name = worker_consumer_group()
    await _ensure_consumer_group(stream, stream_key, group_name)

    trace_parent = get_current_trace() or ""
    messages = [_ready_stream_message(task, trace_parent) for task in tasks]

    # B12: 不在 scheduler_dispatch_epoch() 内直接抛错，禁止无 Master 栅栏派发。
    scheduler_epoch = ready_publish.require_scheduler_dispatch_epoch()
    try:
        redis = await stream._get_client()
        await ready_publish.publish_ready_batch(
            redis,
            worker_id=worker.public_id,
            snapshot=lease_snapshot,
            messages=messages,
            scheduler_fencing_token=scheduler_epoch,
        )
    except ready_publish.LeaseDispatchFenceError:
        raise
    except Exception as e:
        logger.exception("任务写入 Redis 失败")
        # P1-FN-02: 服务端已提交但响应丢失时,直接判失败会触发上游创建
        # retry run → 原消息 + 新 run 双执行。先按 dispatch_id 查重确认
        # (分类/查重/兜底语义见 stream_dedup 模块注释)。
        from antcode_core.infrastructure.redis.stream_dedup import confirm_dispatch_committed

        if not await confirm_dispatch_committed(stream, stream_key, messages, error=e):
            return {"success": False, "error": str(e)}
        logger.warning("P1-FN-02: XADD 响应丢失但派发消息已确认提交,按成功处理: stream={}", stream_key)

    # P1-19: 写入成功后按 group 已 ACK 游标做安全裁剪。裁剪失败仅
    # 记 warning——不影响本批任务已经落 Redis 的语义。
    try:
        await trim_ready_stream(stream, stream_key, group_name)
    except Exception as exc:
        logger.warning("ready stream 裁剪失败 stream={} err={}", stream_key, exc)

    accepted_tasks = [{"task_id": task.get("task_id")} for task in tasks]
    return {
        "success": True,
        "batch_id": batch_id,
        "accepted_count": len(accepted_tasks),
        "rejected_count": 0,
        "accepted_tasks": accepted_tasks,
        "rejected_tasks": [],
        "message": "批量任务已写入 Redis 队列",
    }


async def trim_ready_stream(stream: Any, stream_key: str, group_name: str) -> None:
    """Trim only below the group's proven delivery or pending boundary."""
    try:
        pending_info = await stream.xpending(stream_key, group_name=group_name)
    except Exception as exc:
        logger.warning("xpending 失败，跳过 ready stream 裁剪 stream={} err={}", stream_key, exc)
        return

    pending_count = int(pending_info.get("pending_count", 0))
    if pending_count >= READY_STREAM_PENDING_ALERT_THRESHOLD:
        logger.error(
            "ready stream 积压逼近上限(可能 Worker 离线/卡死): stream={} pending={} threshold={} maxlen={}",
            stream_key,
            pending_count,
            READY_STREAM_PENDING_ALERT_THRESHOLD,
            READY_STREAM_MAXLEN,
        )
    from antcode_core.infrastructure.redis.stream_retention import trim_acknowledged_stream

    client = await stream._get_client()
    await trim_acknowledged_stream(client, stream_key, group_name)


__all__ = [
    "READY_STREAM_MAXLEN",
    "READY_STREAM_PENDING_ALERT_THRESHOLD",
    "publish_ready_batch_to_worker",
    "trim_ready_stream",
]
