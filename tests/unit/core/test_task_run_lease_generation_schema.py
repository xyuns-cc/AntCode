from antcode_core.common.redis_stream_id import MAX_STREAM_ID_LENGTH
from antcode_core.domain.models import TaskRunLeaseGeneration

from scripts.init_db import REQUIRED_TABLES


def test_generation_cutoff_column_fits_maximum_redis_stream_id() -> None:
    field = TaskRunLeaseGeneration._meta.fields_map["log_valid_through_id"]

    assert field.max_length == MAX_STREAM_ID_LENGTH


def test_generation_table_is_required_by_the_schema_validator() -> None:
    assert "task_run_lease_generations" in REQUIRED_TABLES
