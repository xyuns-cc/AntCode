"""链路追踪模块

提供 OpenTelemetry 链路追踪支持（可选）。
"""

import functools
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from loguru import logger


class SpanContext:
    """Span 上下文（简化实现）"""

    def __init__(
        self,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None = None,
    ):
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id


class Span:
    """Span（简化实现）"""

    def __init__(
        self,
        name: str,
        context: SpanContext,
        attributes: dict[str, Any] | None = None,
    ):
        self.name = name
        self.context = context
        self.attributes = attributes or {}
        self._events: list[dict] = []
        self._status: str = "OK"

    def set_attribute(self, key: str, value: Any) -> None:
        """设置属性"""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """添加事件"""
        self._events.append({"name": name, "attributes": attributes or {}})

    def set_status(self, status: str, description: str = "") -> None:
        """设置状态"""
        self._status = status

    def end(self) -> None:
        """结束 Span"""
        logger.debug(
            f"Span ended: {self.name}, trace_id={self.context.trace_id}, "
            f"status={self._status}"
        )


class Tracer:
    """追踪器（简化实现）

    这是一个简化的追踪器实现，用于在没有 OpenTelemetry 时提供基本功能。
    生产环境建议使用完整的 OpenTelemetry SDK。
    """

    def __init__(self, service_name: str = "antcode"):
        self.service_name = service_name
        self._enabled = False

    def enable(self) -> None:
        """启用追踪"""
        self._enabled = True

    def disable(self) -> None:
        """禁用追踪"""
        self._enabled = False

    @contextmanager
    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ):
        """开始一个新的 Span

        Args:
            name: Span 名称
            attributes: 属性

        Yields:
            Span 对象
        """
        import secrets

        if not self._enabled:
            yield None
            return

        context = SpanContext(
            trace_id=secrets.token_hex(16),
            span_id=secrets.token_hex(8),
        )
        span = Span(name, context, attributes)

        try:
            yield span
        except Exception as e:
            span.set_status("ERROR", str(e))
            raise
        finally:
            span.end()

    def trace(
        self,
        name: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Callable:
        """装饰器：追踪函数执行

        Args:
            name: Span 名称，默认使用函数名
            attributes: 属性

        Returns:
            装饰器函数
        """

        def decorator(func: Callable) -> Callable:
            span_name = name or func.__name__

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with self.start_span(span_name, attributes) as span:
                    if span:
                        span.set_attribute("function", func.__name__)
                    return await func(*args, **kwargs)

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                with self.start_span(span_name, attributes) as span:
                    if span:
                        span.set_attribute("function", func.__name__)
                    return func(*args, **kwargs)

            import asyncio

            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            return sync_wrapper

        return decorator


# 全局追踪器
tracer = Tracer()


def init_tracing(
    service_name: str = "antcode",
    endpoint: str | None = None,
    enabled: bool = False,
) -> None:
    """初始化链路追踪

    Args:
        service_name: 服务名称
        endpoint: OTLP 端点（可选）
        enabled: 是否启用
    """
    global tracer
    tracer = Tracer(service_name)

    if enabled:
        tracer.enable()
        logger.info(f"链路追踪已启用: service={service_name}")
    else:
        logger.debug("链路追踪未启用")
