"""W3C TraceContext 工具。

参考: https://www.w3.org/TR/trace-context/

traceparent 格式: ``00-<32hex traceid>-<16hex spanid>-<2hex flags>``

本模块只关心 trace_id / span_id 的生成、解析与 Proto 字段注入,不依赖
任何 OpenTelemetry SDK——AntCode RPC 流量只需要把 traceparent 透传到
位即可。当未来引入 OTel 时,可在此模块之上做适配。

设计要点:

- ``ContextVar`` 隔离: 多个 asyncio 任务/线程并行执行时,各自的 trace
  互不干扰。Worker engine 在 ``_worker_loop`` 处理每个任务前调用
  ``set_current_trace`` 绑定后,该任务内所有 ``loguru`` 日志、
  ``transport.report_result``、``transport.send_log_batch`` 出站时
  ``inject_trace`` 都能拿到一致的 traceparent。
- 与 Proto 的耦合最小: ``inject_trace`` / ``extract_trace_id`` 通过
  ``hasattr/getattr`` 处理 ``trace`` 字段,任何 Proto message 只要有
  ``TraceContext trace`` 字段都能直接用,不需要 import contracts。
"""

from __future__ import annotations

import contextvars
import secrets
from dataclasses import dataclass
from typing import Any

# Async-safe 当前 trace 上下文。
# 每个 asyncio Task 会继承父任务的 ContextVar 快照,但 ``set`` 只影响
# 本任务,天然支持 Worker 同时跑 N 个任务的场景。
_current_trace: contextvars.ContextVar[str | None] = contextvars.ContextVar("antcode_trace", default=None)


@dataclass(frozen=True)
class TraceIds:
    """W3C TraceContext 的三元组(去掉了 vendor-state)。"""

    trace_id: str  # 32 hex
    span_id: str  # 16 hex
    flags: str = "01"  # sampled

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.flags}"


def new_trace() -> TraceIds:
    """生成新的 traceparent(无父 span)。"""
    return TraceIds(
        trace_id=secrets.token_hex(16),
        span_id=secrets.token_hex(8),
    )


def child_span(parent_traceparent: str) -> TraceIds:
    """从父 traceparent 派生子 span(保留 trace_id,新 span_id)。

    输入格式异常时降级为 ``new_trace()``——调用方可以无脑传任意字符串。
    """
    parts = parent_traceparent.split("-")
    if len(parts) != 4 or len(parts[1]) != 32:
        return new_trace()
    flags = parts[3] if len(parts[3]) == 2 else "01"
    return TraceIds(
        trace_id=parts[1],
        span_id=secrets.token_hex(8),
        flags=flags,
    )


def parse_traceparent(traceparent: str) -> TraceIds | None:
    """解析 traceparent 字符串,字段长度不合规直接返回 None。"""
    if not traceparent:
        return None
    parts = traceparent.split("-")
    if len(parts) != 4:
        return None
    if len(parts[1]) != 32 or len(parts[2]) != 16:
        return None
    return TraceIds(trace_id=parts[1], span_id=parts[2], flags=parts[3])


def set_current_trace(traceparent: str) -> contextvars.Token | None:
    """把当前 trace 绑定到 ContextVar,供 logger / 出站点提取。

    返回 ``ContextVar.set`` 的 Token,允许调用方在 finally 块里 reset
    回去(虽然在 ``_worker_loop`` 中每个任务用独立 Task,通常不需要)。

    传入空字符串视为 no-op(用于在不确定是否拿到 trace 的代码路径上无脑调用)。
    需要"清除"当前绑定的 trace 请用 ``clear_current_trace()``。
    """
    if not traceparent:
        return None
    return _current_trace.set(traceparent)


def clear_current_trace() -> None:
    """显式清除当前 ContextVar 上绑定的 trace(回到 ``None``)。"""
    _current_trace.set(None)


def get_current_trace() -> str | None:
    """获取当前 trace。"""
    return _current_trace.get()


def get_current_trace_id() -> str | None:
    """获取当前 trace 的 trace_id 部分(32 hex);未设置或非法时返回 None。"""
    tp = _current_trace.get()
    if not tp:
        return None
    ids = parse_traceparent(tp)
    return ids.trace_id if ids else None


def inject_trace(
    proto_msg: Any,
    traceparent: str = "",
    tracestate: str = "",
) -> None:
    """把 trace 写到 Proto message 的 ``trace`` 字段。

    Proto 约定: ``common.proto`` 中的 ``TraceContext`` message 有
    ``traceparent`` / ``tracestate`` 两个 string,几乎所有业务
    message 都有 ``TraceContext trace = 100;``。

    优先级:
    1. 显式传入的 ``traceparent``
    2. ContextVar 中的当前 trace
    3. 新生成的 ``new_trace().traceparent``

    Proto message 没有 ``trace`` 字段时静默跳过(便于在不知道 message
    形状的情况下统一调用)。
    """
    if not traceparent:
        traceparent = get_current_trace() or new_trace().traceparent
    if not hasattr(proto_msg, "trace"):
        return
    try:
        proto_msg.trace.traceparent = traceparent
        if tracestate:
            proto_msg.trace.tracestate = tracestate
    except (AttributeError, TypeError):
        # 字段类型不符(非 TraceContext)时不抛,保持出站点鲁棒。
        return


def extract_trace_id(proto_msg: Any) -> str | None:
    """从 Proto message 的 ``trace`` 字段提取 trace_id(32 hex)。

    没有 trace 字段、字段为空、或 traceparent 格式非法时返回 None。
    """
    trace_field = getattr(proto_msg, "trace", None)
    if trace_field is None:
        return None
    traceparent = getattr(trace_field, "traceparent", "") or ""
    ids = parse_traceparent(traceparent)
    return ids.trace_id if ids else None


def extract_traceparent(proto_msg: Any) -> str | None:
    """从 Proto message 的 ``trace`` 字段提取完整 traceparent 字符串。"""
    trace_field = getattr(proto_msg, "trace", None)
    if trace_field is None:
        return None
    traceparent = getattr(trace_field, "traceparent", "") or ""
    return traceparent or None
