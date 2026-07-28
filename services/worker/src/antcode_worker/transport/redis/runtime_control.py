"""Direct Redis runtime-control recovery and settlement primitives."""

from __future__ import annotations

from typing import Any

from antcode_core.infrastructure.redis import (
    control_global_stream,
    decode_stream_payload,
    require_runtime_control_request_id,
)
from redis.exceptions import ResponseError

from antcode_worker.transport.redis.control_recovery import PendingControlRecovery
from antcode_worker.transport.redis.runtime_control_evidence import (
    SettlementEvidenceError,
    canonical_result_data,
    decode_settlement_evidence,
    reply_payload,
    runtime_source_identity,
    settlement_fingerprint,
    settlement_key,
    validate_committed_reply,
    validate_result,
    validate_source,
)
from antcode_worker.transport.redis.runtime_control_models import (
    ControlChannel,
    ControlSource,
    RuntimeControlResult,
)
from antcode_worker.transport.redis.runtime_control_store import (
    create_or_validate_marker,
    load_optional_evidence_entry,
    load_required_source_entry,
    persist_reply_once,
    require_pending_owner,
    settlement_expiry_ms,
)

__all__ = [
    "ControlChannel",
    "ControlSource",
    "PendingControlRecovery",
    "RuntimeControlResult",
    "recover_runtime_control_settlement",
    "require_pending_owner",
    "settle_runtime_control_result",
]


async def settle_runtime_control_result(
    redis: Any,
    *,
    source: ControlSource,
    result: RuntimeControlResult,
    expected_reply_stream: str,
    worker_id: str,
    lease_id: str,
    lease_key: str,
    namespace: str,
) -> None:
    """Validate and persist one idempotent runtime-control settlement."""
    require_runtime_control_request_id(result.request_id, worker_id)
    if source.channel.stream_key == control_global_stream(namespace):
        raise ValueError("Direct global runtime_manage 禁止提交运行时结果")
    payload_data = canonical_result_data(result.data)
    validate_result(result, payload_data, expected_reply_stream)
    decoded = await load_required_source_entry(redis, source.channel.stream_key, source.message_id)
    validate_source(decoded, result, expected_reply_stream)
    expires_at_ms = await settlement_expiry_ms(redis, decoded)
    marker_key = settlement_key(namespace, source, worker_id=worker_id)
    existing = await load_optional_evidence_entry(redis, expected_reply_stream, source.message_id)
    marker = await redis.get(marker_key)

    if existing is not None:
        await _accept_committed_reply(
            redis,
            source=source,
            decoded_source=decoded,
            stored_reply=existing,
            marker=marker,
            marker_key=marker_key,
            worker_id=worker_id,
            lease_id=lease_id,
            lease_key=lease_key,
            namespace=namespace,
            expires_at_ms=expires_at_ms,
        )
        return
    if marker is not None:
        await _restore_committed_marker(
            redis,
            source=source,
            decoded_source=decoded,
            marker=marker,
            marker_key=marker_key,
            worker_id=worker_id,
            lease_id=lease_id,
            lease_key=lease_key,
            namespace=namespace,
            expires_at_ms=expires_at_ms,
        )
        return
    fingerprint = settlement_fingerprint(
        source=source,
        result=result,
        data_json=payload_data,
        worker_id=worker_id,
        lease_id=lease_id,
    )
    payload = reply_payload(result, payload_data, fingerprint, lease_id=lease_id)
    await require_pending_owner(redis, source)
    await create_or_validate_marker(
        redis,
        marker_key,
        payload,
        lease_key=lease_key,
        expected_lease_id=lease_id,
        expires_at_ms=expires_at_ms,
    )
    try:
        await persist_reply_once(
            redis,
            reply_stream=expected_reply_stream,
            message_id=source.message_id,
            payload=payload,
            expires_at_ms=expires_at_ms,
        )
    except ResponseError:
        concurrent = await load_optional_evidence_entry(redis, expected_reply_stream, source.message_id)
        if concurrent is None:
            raise
        await _accept_committed_reply(
            redis,
            source=source,
            decoded_source=decoded,
            stored_reply=concurrent,
            marker=await redis.get(marker_key),
            marker_key=marker_key,
            worker_id=worker_id,
            lease_id=lease_id,
            lease_key=lease_key,
            namespace=namespace,
            expires_at_ms=expires_at_ms,
        )
        return


async def recover_runtime_control_settlement(
    redis: Any,
    *,
    source: ControlSource,
    worker_id: str,
    lease_id: str,
    lease_key: str,
    namespace: str,
) -> bool:
    """Return true when a prior generation already persisted this result."""
    decoded = await load_required_source_entry(redis, source.channel.stream_key, source.message_id)
    _, reply_stream = runtime_source_identity(decoded, worker_id, namespace=namespace)
    marker_key = settlement_key(namespace, source, worker_id=worker_id)
    expires_at_ms = await settlement_expiry_ms(redis, decoded)
    existing = await load_optional_evidence_entry(redis, reply_stream, source.message_id)
    marker = await redis.get(marker_key)
    if existing is None:
        if marker is None:
            return False
        await _restore_committed_marker(
            redis,
            source=source,
            decoded_source=decoded,
            marker=marker,
            marker_key=marker_key,
            worker_id=worker_id,
            lease_id=lease_id,
            lease_key=lease_key,
            namespace=namespace,
            expires_at_ms=expires_at_ms,
        )
        return True
    await _accept_committed_reply(
        redis,
        source=source,
        decoded_source=decoded,
        stored_reply=existing,
        marker=marker,
        marker_key=marker_key,
        worker_id=worker_id,
        lease_id=lease_id,
        lease_key=lease_key,
        namespace=namespace,
        expires_at_ms=expires_at_ms,
    )
    return True


async def _accept_committed_reply(
    redis: Any,
    *,
    source: ControlSource,
    decoded_source: dict[str, Any],
    stored_reply: dict[str, Any],
    marker: Any,
    marker_key: str,
    worker_id: str,
    lease_id: str,
    lease_key: str,
    namespace: str,
    expires_at_ms: int,
) -> None:
    committed = validate_committed_reply(
        source,
        decoded_source,
        stored_reply,
        worker_id=worker_id,
        namespace=namespace,
    )
    if marker is None:
        await require_pending_owner(redis, source)
    await create_or_validate_marker(
        redis,
        marker_key,
        committed.payload,
        lease_key=lease_key,
        expected_lease_id=lease_id,
        expires_at_ms=expires_at_ms,
    )
    if not await redis.pexpireat(committed.reply_stream, expires_at_ms):
        raise RuntimeError("Direct 运行时控制结果 TTL 更新失败")


async def _restore_committed_marker(
    redis: Any,
    *,
    source: ControlSource,
    decoded_source: dict[str, Any],
    marker: Any,
    marker_key: str,
    worker_id: str,
    lease_id: str,
    lease_key: str,
    namespace: str,
    expires_at_ms: int,
) -> None:
    payload = decode_settlement_evidence(marker)
    try:
        stored = decode_stream_payload(payload)
    except ValueError as exc:
        raise SettlementEvidenceError("Direct settlement marker reply payload 非法") from exc
    committed = validate_committed_reply(
        source,
        decoded_source,
        stored,
        worker_id=worker_id,
        namespace=namespace,
    )
    if committed.payload != payload:
        raise SettlementEvidenceError("Direct settlement marker canonical reply 冲突")
    await require_pending_owner(redis, source)
    await create_or_validate_marker(
        redis,
        marker_key,
        payload,
        lease_key=lease_key,
        expected_lease_id=lease_id,
        expires_at_ms=expires_at_ms,
    )
    try:
        await persist_reply_once(
            redis,
            reply_stream=committed.reply_stream,
            message_id=source.message_id,
            payload=payload,
            expires_at_ms=expires_at_ms,
        )
    except ResponseError:
        concurrent = await load_optional_evidence_entry(redis, committed.reply_stream, source.message_id)
        if concurrent is None:
            raise
        concurrent_reply = validate_committed_reply(
            source,
            decoded_source,
            concurrent,
            worker_id=worker_id,
            namespace=namespace,
        )
        if concurrent_reply.payload != payload:
            raise SettlementEvidenceError("Direct settlement marker 与并发 reply 冲突")
