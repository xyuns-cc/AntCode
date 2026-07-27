import pytest
from antcode_core.common.security.worker_registration import (
    derive_worker_credentials,
    hash_recovery_secret,
)
from antcode_core.domain.schemas.worker import WorkerRegisterByKeyV2Request
from pydantic import ValidationError


def test_worker_registration_credentials_are_stable_and_domain_separated() -> None:
    first = derive_worker_credentials("a" * 64, "b" * 32, "c" * 64)
    repeated = derive_worker_credentials("a" * 64, "b" * 32, "c" * 64)

    expected_short = "".join(("9d960bbf72a57bd1", "dc5f66dce72d5725"))
    expected_long = "".join(
        (
            "a1d5ccac353251e7",
            "08e8535391b81e1a",
            "f6782d984c080c5e",
            "47cf0c028e5137f5",
        )
    )
    assert first == repeated
    assert first.api_key == expected_short
    assert first.secret_key == expected_long
    assert first.api_key not in first.secret_key
    assert len(first.api_key) == 32
    assert len(first.secret_key) == 64


def test_worker_registration_credentials_change_with_registration_identity() -> None:
    first = derive_worker_credentials("a" * 64, "b" * 32, "c" * 64)
    second = derive_worker_credentials("a" * 64, "d" * 32, "c" * 64)

    assert first != second
    assert hash_recovery_secret("c" * 64) != hash_recovery_secret("d" * 64)


@pytest.mark.parametrize(
    ("install_key_hash", "registration_id", "recovery_secret"),
    [
        ("x" * 64, "b" * 32, "c" * 64),
        ("a" * 64, "B" * 32, "c" * 64),
        ("a" * 64, "b" * 32, "c" * 62),
    ],
)
def test_worker_registration_credentials_reject_malformed_entropy(
    install_key_hash: str,
    registration_id: str,
    recovery_secret: str,
) -> None:
    with pytest.raises(ValueError):
        derive_worker_credentials(install_key_hash, registration_id, recovery_secret)


def test_v2_registration_schema_is_strict_and_masks_recovery_secret() -> None:
    request = WorkerRegisterByKeyV2Request(
        key="INSTALL-KEY",
        name="worker",
        host="127.0.0.1",
        client_timestamp=1,
        client_nonce="nonce-123",
        registration_id="a" * 32,
        recovery_secret="b" * 64,
    )

    assert str(request.recovery_secret) == "**********"
    invalid_payload = request.model_dump(exclude={"recovery_secret"})
    with pytest.raises(ValidationError):
        WorkerRegisterByKeyV2Request(
            **invalid_payload,
            recovery_secret="B" * 64,
            unknown="rejected",
        )
