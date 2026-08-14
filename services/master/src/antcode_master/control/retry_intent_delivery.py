"""Post-commit delivery for PostgreSQL-backed retry intents."""

from __future__ import annotations

from antcode_master.control.retry_intent_guard import RetryIntent


class RetryIntentDeliveryError(RuntimeError):
    """A durable PostgreSQL retry intent could not be published to Redis."""


async def deliver_retry_intent(intent: RetryIntent | None) -> None:
    """Publish a durable intent; PostgreSQL recovery remains authoritative."""
    if intent is None:
        return
    from antcode_master.control.retry_loop import retry_service

    try:
        await retry_service.schedule_intent(intent)
    except Exception as exc:
        raise RetryIntentDeliveryError(
            f"durable retry intent Redis 投递失败: source_run_id={intent.source_run_id}"
        ) from exc


__all__ = ["RetryIntentDeliveryError", "deliver_retry_intent"]
