import pytest
from antcode_core.domain.models import ExecutionStrategy
from antcode_core.domain.schemas.project_unified import UnifiedProjectUpdateRequest
from pydantic import ValidationError


def test_unified_file_fields_include_language() -> None:
    request = UnifiedProjectUpdateRequest(language="go")

    assert request.get_file_fields() == {"language": "go"}


def test_unified_code_entry_point_maps_to_persistence_field() -> None:
    request = UnifiedProjectUpdateRequest(code_entry_point="src/main.ts")

    assert request.get_code_fields() == {"entry_point": "src/main.ts"}


def test_unified_update_rejects_unknown_execution_strategy() -> None:
    with pytest.raises(ValidationError):
        UnifiedProjectUpdateRequest(execution_strategy="random")


def test_unified_update_rejects_task_only_specified_strategy() -> None:
    with pytest.raises(ValidationError, match="specified 仅允许在任务级"):
        UnifiedProjectUpdateRequest(execution_strategy="specified")


def test_unified_update_normalizes_execution_strategy_enum() -> None:
    request = UnifiedProjectUpdateRequest(execution_strategy="fixed")

    assert request.execution_strategy is ExecutionStrategy.FIXED_WORKER
