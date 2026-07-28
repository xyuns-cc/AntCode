import pytest
from antcode_gateway.network import grpc_listen_address


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1:50051"),
        ("gateway.internal", "gateway.internal:50051"),
        ("::1", "[::1]:50051"),
        ("[::]", "[::]:50051"),
    ],
)
def test_grpc_listen_address_uses_configured_host(host: str, expected: str) -> None:
    assert grpc_listen_address(host, 50051) == expected


def test_grpc_listen_address_rejects_empty_host() -> None:
    with pytest.raises(ValueError, match="GRPC_HOST"):
        grpc_listen_address("  ", 50051)
