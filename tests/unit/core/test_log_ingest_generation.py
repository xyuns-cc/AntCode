from types import SimpleNamespace

import pytest
from antcode_core.application.services.workers.log_ingest_generation import (
    EMPTY_STREAM_ID,
    MAX_STREAM_ID_LENGTH,
    parse_stream_id,
    read_log_ingest_cutoff,
    stream_id_not_after,
)

MAX_UINT64_TEXT = "18446744073709551615"


def test_stream_id_comparison_is_numeric_not_lexicographic() -> None:
    assert stream_id_not_after("9-99", "10-0") is True
    assert stream_id_not_after("10-1", "10-0") is False


def test_maximum_redis_stream_id_is_accepted() -> None:
    value = f"{MAX_UINT64_TEXT}-{MAX_UINT64_TEXT}"

    assert len(value) == MAX_STREAM_ID_LENGTH
    assert parse_stream_id(value) == (2**64 - 1, 2**64 - 1)


@pytest.mark.parametrize(
    "value",
    [
        "18446744073709551616-0",
        "0-18446744073709551616",
        "1",
        "1--2",
        "-1-0",
    ],
)
def test_invalid_or_out_of_range_stream_id_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="Stream ID"):
        parse_stream_id(value)


@pytest.mark.asyncio
async def test_missing_stream_has_zero_cutoff() -> None:
    redis = SimpleNamespace(exists=_async_result(0))

    assert await read_log_ingest_cutoff(redis) == EMPTY_STREAM_ID


@pytest.mark.asyncio
async def test_cutoff_accepts_bytes_from_redis_client() -> None:
    redis = SimpleNamespace(
        exists=_async_result(1),
        xinfo_stream=_async_result({b"last-generated-id": b"10-7"}),
    )

    assert await read_log_ingest_cutoff(redis) == "10-7"


def _async_result(value):
    async def result(*_args, **_kwargs):
        return value

    return result
