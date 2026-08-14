from __future__ import annotations

import pytest
from antcode_contracts.runtime_metadata import (
    RUNTIME_DESCRIPTION_MAX_BYTES,
    RUNTIME_KEY_MAX_BYTES,
    validate_runtime_creator,
    validate_runtime_metadata,
)


@pytest.mark.parametrize(
    ("key", "description"),
    [
        ("界" * (RUNTIME_KEY_MAX_BYTES // 3), None),
        (None, "界" * (RUNTIME_DESCRIPTION_MAX_BYTES // 3)),
    ],
)
def test_runtime_metadata_accepts_values_within_utf8_contract(key, description) -> None:
    assert validate_runtime_metadata(key, description) == (key, description)


@pytest.mark.parametrize(
    ("key", "description"),
    [
        ("界" * (RUNTIME_KEY_MAX_BYTES // 3 + 1), None),
        (None, "界" * (RUNTIME_DESCRIPTION_MAX_BYTES // 3 + 1)),
    ],
)
def test_runtime_metadata_rejects_multibyte_values_over_byte_contract(key, description) -> None:
    with pytest.raises(ValueError, match="UTF-8"):
        validate_runtime_metadata(key, description)


@pytest.mark.parametrize("owner_user_id", ["-1", "0", "user-1", "1.0"])
def test_runtime_creator_requires_positive_decimal_owner_id(owner_user_id: str) -> None:
    with pytest.raises(ValueError, match="owner_user_id"):
        validate_runtime_creator("alice", owner_user_id)
