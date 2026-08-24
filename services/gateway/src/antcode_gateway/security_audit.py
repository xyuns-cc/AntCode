"""Gateway 安全审计事件写入。

认证链路(拦截器)与注册链路(ControlService.Register)上的所有凭据拒绝都
汇聚到 ``audit:security`` Stream,供上层做凭据爆破检测。

审计写入永远不能反向影响主流程,因此写失败只降级为 error 日志。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from antcode_core.observability.tracing import parse_traceparent
from loguru import logger

if TYPE_CHECKING:
    from antcode_core.infrastructure.redis.stream_client import StreamClient

# 安全审计 Stream 键名
AUDIT_SECURITY_STREAM = "audit:security"
# 审计 Stream 最大长度（近似裁剪）
AUDIT_SECURITY_MAXLEN = 100_000

# W3C TraceContext metadata header(grpc 元数据按 HTTP/2 习惯全小写)
TRACEPARENT_HEADER = "traceparent"

# P2-#27: 字段截断上限, 防止恶意输入撑爆 audit Stream
MAX_WORKER_ID_CHARS = 128
MAX_PEER_CHARS = 200
MAX_REASON_CHARS = 500

# 事件类型（与 contracts/proto/common.proto 的 SecurityAuditEvent 注释保持一致）
EVENT_API_KEY_INVALID = "api_key_invalid"
EVENT_JWT_INVALID = "jwt_invalid"
EVENT_MTLS_REJECT = "mtls_reject"
EVENT_REGISTER_REJECTED = "register_rejected"
# Deregister 携带空 lease_id：被拒的强制撤销尝试(见 control_service.Deregister)。
EVENT_DEREGISTER_MISSING_GENERATION = "deregister_missing_generation"


def extract_trace_id(metadata: dict[str, Any]) -> str | None:
    """从 gRPC invocation_metadata 提取 W3C trace_id。

    metadata 头大小写在不同 grpc 客户端实现里不一致,这里两种都试。
    格式不合规直接返回 None,不抛——审计路径绝不能因可观测性失败而中断。
    """
    raw = metadata.get(TRACEPARENT_HEADER) or metadata.get(TRACEPARENT_HEADER.upper())
    if not raw:
        return None
    ids = parse_traceparent(str(raw))
    return ids.trace_id if ids else None


def sanitize_worker_id(worker_id: str | None) -> str:
    """worker_id 进入日志/审计前的安全清洗:剥换行 + 限长,防注入。"""
    return (worker_id or "").replace("\n", "\\n").replace("\r", "\\r")[:MAX_WORKER_ID_CHARS]


@dataclass(frozen=True)
class SecurityAuditEvent:
    """一条安全审计事件。

    Attributes:
        event_type: 事件类型,取本模块的 ``EVENT_*`` 常量。
        worker_id: 关联的 worker_id,可空。
        peer: 对端标识(gRPC peer / 代理头 / mTLS CN),可空。
        reason: 简短失败原因。
        trace_id: W3C trace_id(32 hex),空时事件不带该字段。
    """

    event_type: str
    worker_id: str = ""
    peer: str = ""
    reason: str = ""
    trace_id: str | None = None

    def to_fields(self) -> dict[str, str]:
        """序列化成 Stream 字段,同时做长度截断。"""
        fields = {
            "event_type": self.event_type,
            "worker_id": sanitize_worker_id(self.worker_id),
            "peer": str(self.peer or "")[:MAX_PEER_CHARS],
            "reason": str(self.reason or "")[:MAX_REASON_CHARS],
            "ts": str(int(time.time() * 1000)),
        }
        if self.trace_id:
            fields["trace_id"] = self.trace_id
        return fields


class SecurityAuditor:
    """把安全事件写入 ``audit:security`` Stream。

    ``stream`` 为 None 时跳过写入:未接 Redis 的单测/本地运行不会因为审计
    而失败。生产由 ``main._configure_interceptors`` 显式注入 StreamClient。
    """

    def __init__(self, stream: StreamClient | None = None) -> None:
        self._stream = stream

    async def emit(self, event: SecurityAuditEvent) -> None:
        if self._stream is None:
            return
        try:
            await self._stream.xadd(
                AUDIT_SECURITY_STREAM,
                event.to_fields(),
                maxlen=AUDIT_SECURITY_MAXLEN,
                approximate=True,
            )
        except Exception as exc:  # noqa: BLE001 - 审计失败不影响主流程
            logger.exception(f"写入安全审计 Stream 失败: event_type={event.event_type}, error={exc}")


__all__ = [
    "AUDIT_SECURITY_MAXLEN",
    "AUDIT_SECURITY_STREAM",
    "EVENT_API_KEY_INVALID",
    "EVENT_DEREGISTER_MISSING_GENERATION",
    "EVENT_JWT_INVALID",
    "EVENT_MTLS_REJECT",
    "EVENT_REGISTER_REJECTED",
    "SecurityAuditEvent",
    "SecurityAuditor",
    "extract_trace_id",
    "sanitize_worker_id",
]
