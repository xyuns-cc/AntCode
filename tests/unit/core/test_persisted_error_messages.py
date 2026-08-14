from types import SimpleNamespace

from antcode_core.application.services.task_result_commit import _apply_runtime_update
from antcode_core.common.error_messages import (
    ERROR_MESSAGE_TRUNCATION_SUFFIX,
    MAX_PERSISTED_ERROR_MESSAGE_BYTES,
    normalize_persisted_error_message,
)
from antcode_core.domain.models.enums import RuntimeStatus
from antcode_core.domain.models.task_run import TaskRun


def test_persisted_error_message_redacts_credentials() -> None:
    message = "request failed: postgresql://user:top-secret@db.example/app"

    normalized = normalize_persisted_error_message(message)

    assert normalized == "request failed: postgresql://user:***@db.example/app"
    assert "top-secret" not in normalized


def test_persisted_error_message_has_explicit_length_boundary() -> None:
    normalized = normalize_persisted_error_message("界" * MAX_PERSISTED_ERROR_MESSAGE_BYTES)

    assert normalized is not None
    assert len(normalized.encode("utf-8")) <= MAX_PERSISTED_ERROR_MESSAGE_BYTES
    assert normalized.endswith(ERROR_MESSAGE_TRUNCATION_SUFFIX)


def test_persisted_error_message_redacts_before_truncating() -> None:
    marker = "credential-that-must-not-survive"
    message = f"password={marker} " + ("界" * MAX_PERSISTED_ERROR_MESSAGE_BYTES)

    normalized = normalize_persisted_error_message(message)

    assert normalized is not None
    assert marker not in normalized
    assert len(normalized.encode("utf-8")) <= MAX_PERSISTED_ERROR_MESSAGE_BYTES


def test_result_commit_applies_error_security_boundary() -> None:
    request = SimpleNamespace(
        runtime_status=RuntimeStatus.FAILED,
        status_at=None,
        exit_code=1,
        error_message="token=abcdefghijklmnopqrstuvwx",
    )
    execution = SimpleNamespace(
        runtime_status=RuntimeStatus.RUNNING,
        start_time=None,
        end_time=None,
        duration_seconds=None,
    )
    updates: dict[str, object] = {}

    _apply_runtime_update(execution, request, updates)

    assert updates["error_message"] == "token=***REDACTED***"


def test_task_run_field_blocks_direct_orm_bypass() -> None:
    marker = "field-level-secret"
    field = TaskRun._meta.fields_map["error_message"]

    normalized = field.to_db_value(f"password={marker}", TaskRun)

    assert normalized == "password=***REDACTED***"
    assert marker not in normalized
