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


def test_file_git_target_is_rejected_without_dns() -> None:
    with pytest.raises(ValueError, match="仅支持"):
        git_url_security.validate_git_url("file:///tmp/e2e-repo")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:token@example.com/repo.git",
        "https://token@example.com/repo.git",
        "ssh://git:password@example.com/repo.git",
    ],
)
def test_embedded_git_credentials_are_rejected_without_dns(monkeypatch, url: str) -> None:
    def unexpected_dns(*_args, **_kwargs):
        pytest.fail("credential-bearing URL must be rejected before DNS resolution")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)

    with pytest.raises(ValueError, match="访问令牌|密码"):
        git_url_security.validate_git_url(url)


def test_ssh_username_without_password_remains_supported(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )

    assert git_url_security.validate_git_url("ssh://git@example.com/repo.git") == "ssh://git@example.com/repo.git"


def test_cgnat_shared_address_is_rejected() -> None:
    # 100.64.0.0/10 是 CGNAT/共享地址(含阿里云 metadata 邻域),
    # is_private=False 但绝非公网可路由目标。
    with pytest.raises(ValueError, match="私网|本地"):
        git_url_security.validate_git_url("ssh://git@100.64.1.1/repo.git")


def test_public_git_host_resolves_only_to_public_addresses(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )

    assert git_url_security.validate_git_url("https://example.com/repo.git") == "https://example.com/repo.git"

    endpoint = git_url_security.resolve_git_url("https://example.com/repo.git")
    assert endpoint.curl_resolve_value() == "example.com:443:93.184.216.34"


def test_webhook_connection_uses_resolved_address_without_changing_tls_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
    )

    endpoint = git_url_security.resolve_webhook_url("https://example.com:8443/hooks?id=1")

    assert endpoint.pinned_http_url() == "https://93.184.216.34:8443/hooks?id=1"
    assert endpoint.host_header() == "example.com:8443"
    assert endpoint.host == "example.com"


def test_dns_rebinding_to_private_address_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))],
    )

    with pytest.raises(ValueError, match="解析到私网"):
        git_url_security.validate_git_url("https://example.com/repo.git")


@pytest.mark.parametrize(
    "host",
    ["169.254.169.254", "100.100.100.200", "fd00:ec2::254", "metadata.google.internal"],
)
def test_metadata_targets_stay_blocked_when_private_nodes_are_allowed(monkeypatch, host: str) -> None:
    monkeypatch.setattr(git_url_security.settings, "ALLOW_PRIVATE_NODES", True)

    with pytest.raises(ValueError, match="元数据"):
        git_url_security.resolve_host_addresses(host)


def test_dns_answer_to_metadata_is_always_rejected(monkeypatch) -> None:
    monkeypatch.setattr(git_url_security.settings, "ALLOW_PRIVATE_NODES", True)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))],
    )

    with pytest.raises(ValueError, match="元数据"):
        git_url_security.resolve_host_addresses("example.com")
