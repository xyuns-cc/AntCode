"""按 RPC cardinality 给 gRPC handler 包上限流检查。

只负责 grpc 侧的 handler 装配；扣费与拒绝策略由传入的 ``check`` 回调决定
（见 ``RateLimitInterceptor._check``）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import grpc

# ``async def (context, method) -> None``，超限时 abort(RESOURCE_EXHAUSTED)
RateLimitCheck = Callable[[Any, str], Awaitable[None]]


def wrap_handler(original: grpc.RpcMethodHandler, method: str, check: RateLimitCheck) -> grpc.RpcMethodHandler:
    """按 RPC 类型分发到对应的限流包装器。"""
    if original.unary_unary is not None:
        return _wrap_unary_unary(original, method, check)
    if original.unary_stream is not None:
        return _wrap_unary_stream(original, method, check)
    if original.stream_unary is not None:
        return _wrap_stream_unary(original, method, check)
    if original.stream_stream is not None:
        return _wrap_stream_stream(original, method, check)
    return original


async def _metered_requests(request_iterator, context, method: str, *, check: RateLimitCheck):
    """P1-GW-05: client-streaming 逐帧计费。

    此前只在建流时收费一次，持有有效凭据的客户端可用单条
    StreamStatus/StreamLogs/StreamSpiderData/Artifact upload 流持续写
    Redis/数据库绕过请求桶。现在每帧过一次全局+per-key 桶，超限
    abort(RESOURCE_EXHAUSTED)，worker 按普通流错误退避重试。
    """
    async for request in request_iterator:
        await check(context, method)
        yield request


def _wrap_unary_unary(original: grpc.RpcMethodHandler, method: str, check: RateLimitCheck) -> grpc.RpcMethodHandler:
    async def handler(request, context):
        await check(context, method)
        return await original.unary_unary(request, context)

    return grpc.unary_unary_rpc_method_handler(
        handler,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


def _wrap_unary_stream(original: grpc.RpcMethodHandler, method: str, check: RateLimitCheck) -> grpc.RpcMethodHandler:
    async def handler(request, context):
        await check(context, method)
        async for response in original.unary_stream(request, context):
            yield response

    return grpc.unary_stream_rpc_method_handler(
        handler,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


def _wrap_stream_unary(original: grpc.RpcMethodHandler, method: str, check: RateLimitCheck) -> grpc.RpcMethodHandler:
    async def handler(request_iterator, context):
        await check(context, method)
        metered = _metered_requests(request_iterator, context, method, check=check)
        return await original.stream_unary(metered, context)

    return grpc.stream_unary_rpc_method_handler(
        handler,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


def _wrap_stream_stream(original: grpc.RpcMethodHandler, method: str, check: RateLimitCheck) -> grpc.RpcMethodHandler:
    async def handler(request_iterator, context):
        await check(context, method)
        metered = _metered_requests(request_iterator, context, method, check=check)
        async for response in original.stream_stream(metered, context):
            yield response

    return grpc.stream_stream_rpc_method_handler(
        handler,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


__all__ = ["RateLimitCheck", "wrap_handler"]
