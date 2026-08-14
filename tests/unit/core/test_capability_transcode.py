import json
from unittest.mock import MagicMock

import pytest
from antcode_contracts.capabilities import MAX_CAPABILITY_VALUE_BYTES, validate_capabilities
from antcode_contracts.transcode import decode_capabilities, encode_capabilities


def test_capability_wire_round_trip_preserves_types() -> None:
    encoded = encode_capabilities(
        {
            "task_types": ["code", "rule"],
            "curl_cffi": {"enabled": True},
        }
    )

    assert decode_capabilities(encoded) == {
        "task_types": ["code", "rule"],
        "curl_cffi": {"enabled": True},
    }


def test_capability_wire_rejects_oversized_values() -> None:
    oversized = "x" * (MAX_CAPABILITY_VALUE_BYTES + 1)

    with pytest.raises(ValueError, match="size limit"):
        encode_capabilities({"oversized": oversized})


def test_capability_validation_enforces_utf8_wire_size() -> None:
    exact_limit = "x" * (MAX_CAPABILITY_VALUE_BYTES - 2)
    oversized = "界" * ((MAX_CAPABILITY_VALUE_BYTES - 2) // 3 + 1)

    assert validate_capabilities({"exact": exact_limit}) == {"exact": exact_limit}

    with pytest.raises(ValueError, match="size limit"):
        validate_capabilities({"oversized": oversized})


@pytest.mark.parametrize("task_types", ["code", [""], [1]])
def test_capability_wire_rejects_invalid_task_types(task_types) -> None:
    with pytest.raises(ValueError, match="task_types"):
        encode_capabilities({"task_types": task_types})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_capabilities_are_rejected_during_encode(value: float) -> None:
    with pytest.raises(ValueError, match="finite standard JSON"):
        encode_capabilities({"load": value})


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_non_finite_capabilities_are_rejected_during_decode(value: str) -> None:
    with pytest.raises(ValueError):
        decode_capabilities({"load": value})


def test_capability_count_is_rejected_before_json_decode(monkeypatch) -> None:
    decoder = MagicMock(wraps=json.loads)
    monkeypatch.setattr(json, "loads", decoder)
    payload = {f"cap-{index}": "not-json" for index in range(17)}

    with pytest.raises(ValueError, match="too many"):
        decode_capabilities(payload)

    decoder.assert_not_called()
