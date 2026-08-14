"""SSRF-safe SMTP target validation and synchronous delivery primitives."""

from __future__ import annotations

import smtplib
import socket
import ssl
from dataclasses import dataclass, field

from antcode_contracts.network_security import (
    resolve_host_addresses as resolve_network_host_addresses,
)

SMTP_TIMEOUT_SECONDS = 10
SMTP_READY_CODE = 220


@dataclass(frozen=True)
class SMTPDeliveryConfig:
    """Immutable SMTP credentials and transport settings."""

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    use_ssl: bool = True


@dataclass(frozen=True)
class ResolvedSMTPTarget:
    """Original TLS identity paired with a prevalidated connection address."""

    host: str
    port: int
    address: str


def validate_smtp_host(host: str) -> str:
    """Normalize and validate a configured SMTP host as publicly routable."""
    normalized = _normalize_smtp_host(host)
    if not normalized:
        return ""
    resolve_network_host_addresses(normalized, allow_private=False)
    return normalized


def resolve_smtp_target(config: SMTPDeliveryConfig) -> ResolvedSMTPTarget:
    """Resolve again immediately before connecting to prevent DNS rebinding."""
    host = _normalize_smtp_host(config.host)
    if not host:
        raise ValueError("SMTP 目标主机不能为空")
    addresses = resolve_network_host_addresses(host, allow_private=False)
    return ResolvedSMTPTarget(host=host, port=config.port, address=addresses[0])


def _normalize_smtp_host(host: str) -> str:
    return (host or "").strip().lower().rstrip(".")


def deliver_smtp_message(
    config: SMTPDeliveryConfig,
    *,
    recipient_email: str,
    message: str,
) -> None:
    """Deliver one message through an address pinned at connection time."""
    target = resolve_smtp_target(config)
    context = ssl.create_default_context()
    server = _connect_smtp(target, use_ssl=config.use_ssl, context=context)
    try:
        server.login(config.username, config.password)
        server.sendmail(config.username, [recipient_email], message)
        server.quit()
    finally:
        server.close()


def _connect_smtp(
    target: ResolvedSMTPTarget,
    *,
    use_ssl: bool,
    context: ssl.SSLContext,
) -> smtplib.SMTP:
    server = _new_smtp_server(use_ssl=use_ssl, context=context)
    # smtplib uses _host as TLS server_hostname. TCP connects to the pinned IP,
    # while SNI and certificate verification therefore retain the original host.
    setattr(server, "_host", target.host)
    try:
        code, response = server.connect(target.address, target.port)
        if code != SMTP_READY_CODE:
            raise smtplib.SMTPConnectError(code, response)
        if not use_ssl:
            server.starttls(context=context)
        return server
    except Exception:
        server.close()
        raise


def _new_smtp_server(*, use_ssl: bool, context: ssl.SSLContext) -> smtplib.SMTP:
    if use_ssl:
        return _PinnedSMTPSSL(timeout=SMTP_TIMEOUT_SECONDS, context=context)
    return smtplib.SMTP(timeout=SMTP_TIMEOUT_SECONDS)


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    """Connect to a pinned address while verifying TLS against ``_host``."""

    def _get_socket(self, host: str, port: int, timeout):
        if timeout is not None and not timeout:
            raise ValueError("Non-blocking socket (timeout=0) is not supported")
        plain_socket = socket.create_connection((host, port), timeout, self.source_address)
        return self.context.wrap_socket(plain_socket, server_hostname=getattr(self, "_host"))


__all__ = [
    "SMTPDeliveryConfig",
    "deliver_smtp_message",
    "resolve_smtp_target",
    "validate_smtp_host",
]
