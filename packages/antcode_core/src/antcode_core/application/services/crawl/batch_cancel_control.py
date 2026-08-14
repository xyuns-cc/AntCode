"""Worker control-plane delivery for Crawl batch run cancellation.

从 ``batch_dispatcher_service`` 搬出的 control 投递细节：查 run 归属的
Worker、写取消 control 事件。搬运不改行为，取消的编排与 PEL 重投语义
仍在 ``CrawlBatchDispatcherService`` 里。
"""

from __future__ import annotations

from loguru import logger


async def send_batch_run_cancel(run_id: str, reason: str) -> bool | None:
    """P1-07：复用单 run 取消的 control 通道给 batch 里的每个活跃 run。

    ``None`` 表示无需/无法发送 control——尚未分配 Worker，或分配的
    Worker 记录已删除（control 永久不可达），调用方可直接置
    CANCELLED；``False`` 表示已分配但 control 暂时无法投递（如 Redis
    不可用），此时调用方不得写 CANCELLED，保留 PEL 重投。
    """
    from antcode_core.application.services.runtime.runtime_control_service import (
        write_control_event,
    )
    from antcode_core.application.services.workers.worker_service import worker_service
    from antcode_core.domain.models import TaskRun
    from antcode_core.infrastructure.redis import get_redis_client
    from antcode_core.infrastructure.redis.control_plane import (
        build_cancel_control_payload,
        control_stream,
    )

    exe = await TaskRun.filter(run_id=run_id).only("id", "run_id", "worker_id").first()
    if exe is None or not exe.worker_id:
        return None
    worker = await worker_service.get_worker_by_id(exe.worker_id)
    if worker is None:
        # C4: Worker 记录已删除 → control 永久不可达。若按 False 处理，
        # _on_batch_cancelled 会永远重投失败，幽灵活跃 run 阻塞项目
        # 删除。跳过 control，允许直接置 CANCELLED。
        logger.warning(
            f"batch 取消: 分配的 Worker 记录已不存在，跳过 control 直接取消: run_id={run_id} worker_id={exe.worker_id}"
        )
        return None
    redis = await get_redis_client()
    if redis is None:
        return False
    payload = build_cancel_control_payload(run_id=run_id, reason=reason)
    await write_control_event(redis, control_stream(worker.public_id), payload)
    return True


__all__ = ["send_batch_run_cancel"]
