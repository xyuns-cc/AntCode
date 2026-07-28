"""Gateway data 面入口守卫（P1-SEC-04 / receipt 归属）。"""

from __future__ import annotations

import grpc
from antcode_core.infrastructure.redis import task_ready_stream

# 复审 P1-SEC-04: 单帧 TaskStatus 上限——status/result 是小元数据，50MiB
# gRPC 上限对它过大，受控 Worker 可用超大 result/error 正文灌满 Redis/PG。
# 大体积产物必须走 artifact 通道（那侧有 per-run 配额）。
MAX_STATUS_FRAME_BYTES = 1024 * 1024


async def require_bounded_status_frame(context: grpc.aio.ServicerContext, task_status) -> bool:
    frame_bytes = task_status.ByteSize()
    if frame_bytes <= MAX_STATUS_FRAME_BYTES:
        return True
    await context.abort(
        grpc.StatusCode.INVALID_ARGUMENT,
        f"status frame too large: {frame_bytes} > {MAX_STATUS_FRAME_BYTES}",
    )
    return False


async def require_owned_receipt(context: grpc.aio.ServicerContext, worker_id: str, receipt_id: str) -> bool:
    """receipt 必须存在且其 stream 归属已认证 worker。"""
    if not receipt_id:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "receipt_id 缺失")
        return False
    receipt_stream = receipt_id.split("|", 1)[0]
    if receipt_stream != task_ready_stream(worker_id):
        await context.abort(
            grpc.StatusCode.PERMISSION_DENIED,
            "receipt stream does not belong to authenticated worker",
        )
        return False
    return True


__all__ = ["MAX_STATUS_FRAME_BYTES", "require_bounded_status_frame", "require_owned_receipt"]
