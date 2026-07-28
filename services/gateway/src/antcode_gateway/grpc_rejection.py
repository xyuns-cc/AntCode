"""Cardinality-preserving gRPC rejection handlers."""

from __future__ import annotations

from collections.abc import Callable

import grpc


def make_rejection_handler(
    original: grpc.RpcMethodHandler | None,
    reject: Callable,
) -> grpc.RpcMethodHandler:
    if original is None or original.unary_unary is not None:
        return grpc.unary_unary_rpc_method_handler(
            reject,
            request_deserializer=_deserializer(original),
            response_serializer=_serializer(original),
        )
    if original.unary_stream is not None:
        return grpc.unary_stream_rpc_method_handler(
            reject,
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
        reject,
        request_deserializer=original.request_deserializer,
        response_serializer=original.response_serializer,
    )


def _deserializer(handler):
    return handler.request_deserializer if handler is not None else None


def _serializer(handler):
    return handler.response_serializer if handler is not None else None


def make_rate_limit_rejection(original, result) -> grpc.RpcMethodHandler:
    async def reject(_request, context):
        context.set_trailing_metadata(
            [
                ("retry-after", str(int(result.retry_after) + 1)),
                ("x-ratelimit-remaining", "0"),
                ("x-ratelimit-reset", str(int(result.reset_at))),
            ]
        )
        await context.abort(
            grpc.StatusCode.RESOURCE_EXHAUSTED,
            f"请求过于频繁，请在 {result.retry_after:.1f} 秒后重试",
        )

    return make_rejection_handler(original, reject)


__all__ = ["make_rate_limit_rejection", "make_rejection_handler"]
