from datetime import UTC, datetime
from types import SimpleNamespace

from antcode_core.domain.models.enums import (
    DispatchStatus,
    ProjectStatus,
    ProjectType,
    ScheduleType,
    TaskStatus,
    TaskType,
)
from antcode_core.domain.schemas.task import TaskResponse
from antcode_web_api.response import (
    ExecutionResponseBuilder,
    ProjectResponseBuilder,
    TaskResponseBuilder,
)

SUCCESS_COUNT = 11
FAILURE_COUNT = 4
MAX_INSTANCES = 2
TIMEOUT_SECONDS = 900
RETRY_DELAY_SECONDS = 15
PAGE_LIMIT = 7
RUN_RETRY_COUNT = 2


def test_project_response_preserves_worker_environment_fields() -> None:
    response = ProjectResponseBuilder.build_detail(_project())

    assert response.env_location == "worker"
    assert response.worker_id == "worker-public-7"
    assert response.worker_env_name == "project-runtime"


def test_task_response_preserves_outcome_counts() -> None:
    response = TaskResponseBuilder.build_detail(_task())

    assert response.success_count == SUCCESS_COUNT
    assert response.failure_count == FAILURE_COUNT


def test_task_response_preserves_execution_configuration() -> None:
    response = TaskResponseBuilder.build_detail(_task())

    assert response.max_instances == MAX_INSTANCES
    assert response.timeout_seconds == TIMEOUT_SECONDS
    assert response.retry_count == 0
    assert response.retry_delay == RETRY_DELAY_SECONDS
    assert response.execution_params == {"page_limit": PAGE_LIMIT}
    assert response.environment_vars == {"APP_MODE": "test"}


def test_task_response_nullable_schedule_fields_match_schema() -> None:
    response = TaskResponseBuilder.build_detail(_task())

    validated = TaskResponse.model_validate(response.model_dump())

    assert validated.cron_expression is None
    assert validated.interval_seconds is None
    assert validated.scheduled_time is None
    assert validated.last_run_time is None
    assert validated.next_run_time is None


def test_task_list_omits_decrypted_execution_configuration() -> None:
    response = TaskResponseBuilder.build_list([_task()])[0]

    assert response.retry_count == 0
    assert response.execution_params is None
    assert response.environment_vars is None


def test_task_run_response_preserves_retry_count_and_null_exit_code() -> None:
    response = ExecutionResponseBuilder.build_detail(_execution())

    assert response.retry_count == RUN_RETRY_COUNT
    assert response.exit_code is None
    assert response.model_dump()["exit_code"] is None


def _project() -> SimpleNamespace:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    return SimpleNamespace(
        public_id="project-public-1",
        name="response-project",
        description=None,
        type=ProjectType.FILE,
        status=ProjectStatus.ACTIVE,
        tags=[],
        dependencies=[],
        created_at=timestamp,
        updated_at=timestamp,
        created_by_public_id="user-public-1",
        created_by_username="owner",
        star_count=0,
        env_location="worker",
        worker_id="worker-public-7",
        worker_env_name="project-runtime",
    )


def _task() -> SimpleNamespace:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    return SimpleNamespace(
        public_id="task-public-1",
        name="response-task",
        description="",
        project_public_id="project-public-1",
        schedule_type=ScheduleType.ONCE,
        is_active=True,
        task_type=TaskType.FILE,
        status=TaskStatus.PENDING,
        cron_expression=None,
        interval_seconds=None,
        scheduled_time=None,
        max_instances=MAX_INSTANCES,
        timeout_seconds=TIMEOUT_SECONDS,
        retry_count=0,
        retry_delay=RETRY_DELAY_SECONDS,
        execution_params={"page_limit": PAGE_LIMIT},
        environment_vars={"APP_MODE": "test"},
        last_run_time=None,
        next_run_time=None,
        created_at=timestamp,
        updated_at=timestamp,
        created_by_public_id="user-public-1",
        created_by_username="owner",
        success_count=SUCCESS_COUNT,
        failure_count=FAILURE_COUNT,
    )


def _execution() -> SimpleNamespace:
    return SimpleNamespace(
        public_id="execution-public-1",
        run_id="run-1",
        task_public_id="task-public-1",
        start_time=None,
        end_time=None,
        duration_seconds=None,
        status=TaskStatus.RUNNING,
        dispatch_status=DispatchStatus.ACKED,
        runtime_status=None,
        dispatch_updated_at=None,
        runtime_updated_at=None,
        exit_code=None,
        error_message=None,
        result_data=None,
        retry_count=RUN_RETRY_COUNT,
        worker_public_id=None,
    )
