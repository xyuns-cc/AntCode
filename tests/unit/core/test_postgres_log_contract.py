import inspect

from antcode_core.application.services.logs.task_log_service import TaskLogService
from antcode_core.domain.models.task_run import TaskRun


def test_task_run_model_does_not_store_log_file_paths():
    fields = set(TaskRun._meta.fields_map)

    assert "log_file_path" not in fields
    assert "error_log_path" not in fields


def test_task_log_service_uses_run_id_and_log_type_contract():
    assert not hasattr(TaskLogService, "generate_log_paths")

    write_params = list(inspect.signature(TaskLogService.write_log).parameters)
    read_params = list(inspect.signature(TaskLogService.read_log).parameters)

    assert write_params[:4] == ["self", "run_id", "log_type", "content"]
    assert read_params[:3] == ["self", "run_id", "log_type"]
