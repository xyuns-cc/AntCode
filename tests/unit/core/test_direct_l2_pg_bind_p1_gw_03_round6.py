"""Lease rebind authority belongs to ownership claim, never result ingest."""

from antcode_core.application.services.task_run_service import TaskRunService
from antcode_core.application.services.workers.run_ownership_service import (
    bind_worker_run_lease_generation,
)


def test_result_service_exposes_no_lease_rebind_operation() -> None:
    service = TaskRunService()

    assert not hasattr(service, "_bind_lease_generation")


def test_ownership_service_is_the_single_generation_bind_entrypoint() -> None:
    assert callable(bind_worker_run_lease_generation)
