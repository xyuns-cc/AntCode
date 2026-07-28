import pytest
from antcode_core.domain.schemas.worker import WorkerInstallKeyRequest
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("10.1.2.3", "10.1.2.3"),
        ("10.1.2.3/24", "10.1.2.0/24"),
        ("2001:db8::7/64", "2001:db8::/64"),
        ("", None),
    ],
)
def test_install_key_source_accepts_only_canonical_ip_or_cidr(source: str, expected: str | None) -> None:
    request = WorkerInstallKeyRequest(os_type="linux", allowed_source=source)

    assert request.allowed_source == expected


def test_install_key_source_rejects_hostname() -> None:
    with pytest.raises(ValidationError, match="不支持主机名"):
        WorkerInstallKeyRequest(os_type="linux", allowed_source="worker.example.com")
