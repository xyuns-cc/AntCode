from antcode_core.application.services.scheduler.execution_resolver import (
    ExecutionResolver,
    execution_resolver,
)
from antcode_master.dispatch import selector


def test_master_execution_selector_uses_core_strict_resolver():
    assert selector.ExecutionResolver is ExecutionResolver
    assert selector.execution_resolver is execution_resolver
