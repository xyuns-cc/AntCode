import socket

import pytest
from antcode_contracts import network_security

PUBLIC_IPV4 = "93.184.216.34"
PUBLIC_IPV6 = "2606:2800:220:1:248:1893:25c8:1946"


def _dns_answers(*addresses: str):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 0),
        )
        for address in addresses
    ]


def test_empty_host_is_rejected() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        network_security.resolve_host_addresses(" ", allow_private=False)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "100.64.0.1", "198.18.0.1"])
def test_non_public_ip_literals_are_recognized(address: str) -> None:
    assert network_security.is_non_public_address(address)


def test_public_dns_answers_are_deduplicated_and_stably_sorted(monkeypatch) -> None:
    monkeypatch.setattr(
        network_security.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _dns_answers(PUBLIC_IPV6, PUBLIC_IPV4, PUBLIC_IPV4),
    )

    assert network_security.resolve_host_addresses("example.com", allow_private=False) == (
        PUBLIC_IPV4,
        PUBLIC_IPV6,
    )


def test_mixed_public_and_private_dns_answers_are_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        network_security.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _dns_answers(PUBLIC_IPV4, "127.0.0.1"),
    )

    with pytest.raises(ValueError, match="解析到私网"):
        network_security.resolve_host_addresses("example.com", allow_private=False)


def test_allow_private_accepts_private_dns_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        network_security.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _dns_answers("10.0.0.1"),
    )

    assert network_security.resolve_host_addresses("internal.example", allow_private=True) == ("10.0.0.1",)


@pytest.mark.parametrize("host", ["169.254.169.254", "metadata.google.internal", "::ffff:169.254.169.254"])
def test_metadata_literals_are_always_rejected(host: str) -> None:
    with pytest.raises(ValueError, match="元数据"):
        network_security.resolve_host_addresses(host, allow_private=True)


def test_metadata_dns_answer_is_rejected_when_private_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr(
        network_security.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _dns_answers("169.254.169.254"),
    )

    with pytest.raises(ValueError, match="元数据"):
        network_security.resolve_host_addresses("example.com", allow_private=True)
