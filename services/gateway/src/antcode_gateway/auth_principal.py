"""Gateway authentication result and active RPC principal context."""

from contextvars import ContextVar
from dataclasses import dataclass

import grpc

AUTHENTICATED_WORKER_ID: ContextVar[str | None] = ContextVar(
    "antcode_authenticated_worker_id",
    default=None,
)


@dataclass
class AuthResult:
    """Authentication outcome used by the Gateway interceptor."""

    success: bool
    worker_id: str | None = None
    error: str | None = None
    auth_method: str | None = None


def get_authenticated_worker_id() -> str | None:
    """Return the server-verified Worker identity for the active RPC."""
    return AUTHENTICATED_WORKER_ID.get()


async def require_authenticated_worker(
    context: grpc.aio.ServicerContext,
    declared_worker_id: str | None = None,
) -> str:
    """Require the active RPC principal and optionally bind a request field to it."""
    worker_id = get_authenticated_worker_id()
    if not worker_id:
        await context.abort(
            grpc.StatusCode.UNAUTHENTICATED,
            "authenticated worker identity is missing",
        )
        return ""
    if declared_worker_id and declared_worker_id != worker_id:
        await context.abort(
            grpc.StatusCode.PERMISSION_DENIED,
            "worker_id does not match authenticated principal",
        )
        return ""
    return worker_id
