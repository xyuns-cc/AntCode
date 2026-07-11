import socket

import pytest
from antcode_core.application.services.projects import git_url_security


@pytest.fixture(autouse=True)
def disallow_private_nodes(monkeypatch) -> None:
    monkeypatch.setattr(git_url_security.settings, "ALLOW_PRIVATE_NODES", False)


def test_private_git_target_is_rejected_without_dns() -> None:
    with pytest.raises(ValueError, match="私网"):
        git_url_security.validate_git_url("ssh://git@10.0.0.1/repo.git")


def test_remote_helper_syntax_is_rejected() -> None:
    with pytest.raises(ValueError, match="remote helper"):
        git_url_security.validate_git_url("ext::sh -c id")


def test_public_git_host_resolves_only_to_public_addresses(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )

    assert git_url_security.validate_git_url("https://example.com/repo.git") == "https://example.com/repo.git"


def test_dns_rebinding_to_private_address_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )

    with pytest.raises(ValueError, match="解析到私网"):
        git_url_security.validate_git_url("https://example.com/repo.git")
