import asyncio

import pytest
from antcode_worker.executor.output_stream import OutputByteBudget, OutputReadOptions, read_output_stream

EXPECTED_LINE_COUNT = 2


class _Sink:
    def __init__(self) -> None:
        self.entries = []

    async def write(self, entry) -> None:
        self.entries.append(entry)


def _reader(payload: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_shared_byte_budget_drops_excess_but_drains_both_streams():
    sink = _Sink()
    budget = OutputByteBudget(max_bytes=8)
    sequence = {"stdout": 0, "stderr": 0}
    stdout = OutputReadOptions("run-1", "stdout", 100, sink, sequence, budget)
    stderr = OutputReadOptions("run-1", "stderr", 100, sink, sequence, budget)

    counts = await asyncio.gather(
        read_output_stream(_reader(b"one\ntwo\nthree\n"), stdout),
        read_output_stream(_reader(b"four\nfive\n"), stderr),
    )

    assert counts == [3, 2]
    assert [entry.content for entry in sink.entries] == ["one", "two"]
    assert budget.consumed_bytes == len(b"one\ntwo\nthree\nfour\nfive\n")
    assert budget.exceeded is True


@pytest.mark.asyncio
async def test_non_positive_byte_budget_is_unlimited():
    sink = _Sink()
    options = OutputReadOptions("run-1", "stdout", 10, sink, {"stdout": 0}, OutputByteBudget(0))

    count = await read_output_stream(_reader(b"one\ntwo\n"), options)

    assert count == EXPECTED_LINE_COUNT
    assert [entry.content for entry in sink.entries] == ["one", "two"]
