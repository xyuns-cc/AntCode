import pytest
from antcode_worker.transport.task_message_validation import validate_task_message_payload

VALID_TIMEOUT_SECONDS = 30


def _valid_payload() -> dict:
    return {
        "task_id": "task-1",
        "project_id": "project-1",
        "run_id": "run-1",
        "params": {"key": "value"},
        "environment": {"MODE": "test"},
        "timeout": str(VALID_TIMEOUT_SECONDS),
    }


def test_valid_task_payload_is_normalized_without_mutating_input() -> None:
    payload = _valid_payload()

    result = validate_task_message_payload(payload, allow_integer_strings=True)

    assert result.timeout == VALID_TIMEOUT_SECONDS
    assert result.params == payload["params"]
    assert result.params is not payload["params"]


@pytest.mark.parametrize("field", ["task_id", "project_id", "run_id"])
def test_required_task_identity_rejects_missing_or_empty_values(field: str) -> None:
    payload = _valid_payload()
    payload[field] = ""

    with pytest.raises(ValueError, match=field):
        validate_task_message_payload(payload, allow_integer_strings=True)


@pytest.mark.parametrize("field", ["params", "environment"])
def test_mapping_fields_reject_non_objects(field: str) -> None:
    payload = _valid_payload()
    payload[field] = ["not", "an", "object"]

    with pytest.raises(TypeError, match=field):
        validate_task_message_payload(payload, allow_integer_strings=True)


@pytest.mark.parametrize("timeout", [0, -1, True, "invalid"])
def test_timeout_rejects_non_positive_and_non_integer_values(timeout) -> None:
    payload = _valid_payload()
    payload["timeout"] = timeout

    with pytest.raises((TypeError, ValueError), match="timeout"):
        validate_task_message_payload(payload, allow_integer_strings=True)
