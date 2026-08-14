"""Pure validation helpers for recoverable Worker registration."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network

from antcode_core.domain.schemas.worker import WorkerRegisterByKeyV2Request


def registration_request_hash(request: WorkerRegisterByKeyV2Request) -> str:
    payload = {
        "host": request.host,
        "name": request.name,
        "port": request.port,
        "region": request.region,
        "transport_mode": request.transport_mode,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_matches(source: str, rule: str) -> bool:
    try:
        source_address = ip_address(source)
        if "/" not in rule:
            return source_address == ip_address(rule)
        return source_address in ip_network(rule, strict=False)
    except ValueError:
        return False


def digest_matches(stored: str | None, expected: str) -> bool:
    return bool(stored) and hmac.compare_digest(stored or "", expected)


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = ["as_utc", "digest_matches", "registration_request_hash", "source_matches"]
