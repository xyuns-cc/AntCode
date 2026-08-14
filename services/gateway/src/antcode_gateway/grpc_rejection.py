"""Cardinality-preserving gRPC rejection handlers.

拒绝 handler 必须与原 RPC 的 cardinality 一致，否则客户端会在 codec 层报错。
除此之外，**response-streaming**（``unary_stream`` / ``stream_stream``）的拒绝
handler 还必须是 async generator：外层包装器（如限流拦截器）会对它做
``async for``，拿到 coroutine 会直接 ``TypeError``。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import grpc

# ``async def (request_or_iterator, context) -> None``，内部必须 abort 终止 RPC
RejectCallable = Callable[[Any, Any], Awaitable[None]]


def _streaming_rejection(reject: RejectCallable) -> Callable:
    """把 abort 型 reject 包成 async generator。

    ``reject`` 内部一定会 ``context.abort``（其本身抛异常终止 RPC），所以下面的
    ``yield`` 只用于让解释器把本函数编译成异步生成器，永远不会真的产出元素。
    """

    async def streaming_reject(request_or_iterator, context):
        await reject(request_or_iterator, context)
        return
        yield  # pragma: no cover - 仅用于标记为 async generator

    return streaming_reject


def make_rejection_handler(
    original: grpc.RpcMethodHandler | None,
    reject: RejectCallable,
) -> grpc.RpcMethodHandler:
    if original is None or original.unary_unary is not None:
        return grpc.unary_unary_rpc_method_handler(
            reject,
            request_deserializer=_deserializer(original),
            response_serializer=_serializer(original),
        )
    if original.unary_stream is not None:
        return grpc.unary_stream_rpc_method_handler(
            _streaming_rejection(reject),
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )
    if original.stream_unary is not None:
        return grpc.stream_unary_rpc_method_handler(
            reject,
            request_deserializer=original.request_deserializer,
            response_serializer=original.response_serializer,
        )
    return grpc.stream_stream_rpc_method_handler(
        _streaming_rejection(reject),
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


def _deserializer(handler):
    return handler.request_deserializer if handler is not None else None


def _serializer(handler):
    return handler.response_serializer if handler is not None else None


__all__ = ["make_rejection_handler"]
