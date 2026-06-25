"""Contract tests for ``antcode_core.observability.tracing``.

These are pure-Python unit tests (no Redis / gRPC); they exercise:

- ``new_trace`` produces a well-formed W3C traceparent
- ``child_span`` preserves the trace_id and rotates the span_id
- ``parse_traceparent`` rejects malformed input and accepts well-formed input
- ``inject_trace`` / ``extract_trace_id`` round-trip through a
  fake Proto-like object (``trace.traceparent`` / ``trace.tracestate``)
- ``set_current_trace`` / ``get_current_trace`` are ContextVar-isolated
  across asyncio tasks

The tests do **not** import ``antcode_contracts`` to avoid pulling in the
generated Proto modules (and their grpc runtime dependency) just for these
helpers — a tiny stand-in object is enough to verify the contract.
"""

from __future__ import annotations

import asyncio

import pytest
from antcode_core.observability.tracing import (
    TraceIds,
    child_span,
    clear_current_trace,
    extract_trace_id,
    extract_traceparent,
    get_current_trace,
    get_current_trace_id,
    inject_trace,
    new_trace,
    parse_traceparent,
    set_current_trace,
)


@pytest.fixture(autouse=True)
def _reset_trace_context():
    """Each test starts with the ContextVar cleared.

    The ``set_current_trace`` calls in earlier tests would otherwise leak
    into later ones (asyncio.run inherits the caller's Context).
    """
    clear_current_trace()
    yield
    clear_current_trace()


# ---------------------------------------------------------------------------
# Fake Proto-like fixture — mimics ``data_pb2.TaskDispatch`` (and any other
# message with ``TraceContext trace = 100``) enough to test inject / extract
# without depending on antcode_contracts.
# ---------------------------------------------------------------------------
class _FakeTraceField:
    __slots__ = ("traceparent", "tracestate")

    def __init__(self) -> None:
        self.traceparent: str = ""
        self.tracestate: str = ""


class _FakeProtoMsg:
    """Behaves like a Proto message with a ``trace`` TraceContext field."""

    def __init__(self) -> None:
        self.trace = _FakeTraceField()


class _NoTraceProtoMsg:
    """Proto-like message without a ``trace`` field (e.g. an ACK)."""


# ---------------------------------------------------------------------------
# new_trace / parse_traceparent
# ---------------------------------------------------------------------------
def test_new_trace_format_is_w3c_compliant() -> None:
    ids = new_trace()
    assert isinstance(ids, TraceIds)
    assert len(ids.trace_id) == 32
    assert len(ids.span_id) == 16
    assert ids.flags == "01"

    tp = ids.traceparent
    parts = tp.split("-")
    assert parts[0] == "00"
    assert parts[1] == ids.trace_id
    assert parts[2] == ids.span_id
    assert parts[3] == ids.flags
    # hex sanity (will raise if any segment has non-hex chars)
    int(ids.trace_id, 16)
    int(ids.span_id, 16)


def test_new_trace_is_unique() -> None:
    a = new_trace()
    b = new_trace()
    assert a.trace_id != b.trace_id
    assert a.span_id != b.span_id


def test_parse_traceparent_accepts_well_formed() -> None:
    src = new_trace()
    parsed = parse_traceparent(src.traceparent)
    assert parsed is not None
    assert parsed.trace_id == src.trace_id
    assert parsed.span_id == src.span_id
    assert parsed.flags == src.flags


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-traceparent",
        "00-deadbeef-1234567890123456-01",  # trace_id too short
        "00-" + "a" * 32 + "-deadbeef-01",  # span_id wrong length
        "00-" + "a" * 32 + "-" + "b" * 16,  # missing flags segment
    ],
)
def test_parse_traceparent_rejects_garbage(bad: str) -> None:
    assert parse_traceparent(bad) is None


# ---------------------------------------------------------------------------
# child_span
# ---------------------------------------------------------------------------
def test_child_span_preserves_trace_id_rotates_span_id() -> None:
    parent = new_trace()
    child = child_span(parent.traceparent)
    assert child.trace_id == parent.trace_id
    assert child.span_id != parent.span_id
    assert len(child.span_id) == 16
    assert child.flags == parent.flags


def test_child_span_falls_back_to_new_trace_on_garbage() -> None:
    child = child_span("not-a-real-traceparent")
    # Falls back to a fresh trace rather than raising.
    assert len(child.trace_id) == 32
    assert len(child.span_id) == 16


# ---------------------------------------------------------------------------
# inject_trace / extract_trace_id round-trip
# ---------------------------------------------------------------------------
def test_inject_and_extract_round_trip() -> None:
    msg = _FakeProtoMsg()
    ids = new_trace()
    inject_trace(msg, ids.traceparent, tracestate="foo=bar")

    assert msg.trace.traceparent == ids.traceparent
    assert msg.trace.tracestate == "foo=bar"
    assert extract_trace_id(msg) == ids.trace_id
    assert extract_traceparent(msg) == ids.traceparent


def test_inject_trace_uses_contextvar_when_no_explicit_arg() -> None:
    msg = _FakeProtoMsg()
    ids = new_trace()
    set_current_trace(ids.traceparent)
    inject_trace(msg)
    assert msg.trace.traceparent == ids.traceparent
    assert extract_trace_id(msg) == ids.trace_id


def test_inject_trace_generates_fresh_when_no_context_and_no_arg() -> None:
    msg = _FakeProtoMsg()
    # Run inside its own task so the ContextVar is guaranteed unset.

    async def _runner() -> str | None:
        inject_trace(msg)
        return msg.trace.traceparent

    tp = asyncio.run(_runner())
    assert tp is not None
    assert parse_traceparent(tp) is not None
    assert extract_trace_id(msg) is not None


def test_inject_trace_silently_skips_messages_without_trace_field() -> None:
    msg = _NoTraceProtoMsg()
    ids = new_trace()
    # Must not raise.
    inject_trace(msg, ids.traceparent)
    # And there's no trace field, so extract returns None.
    assert extract_trace_id(msg) is None


def test_extract_trace_id_returns_none_for_empty_field() -> None:
    msg = _FakeProtoMsg()
    # Default-constructed: traceparent is "".
    assert extract_trace_id(msg) is None
    assert extract_traceparent(msg) is None


# ---------------------------------------------------------------------------
# ContextVar isolation across asyncio tasks
# ---------------------------------------------------------------------------
def test_contextvar_isolation_between_async_tasks() -> None:
    """Two concurrent tasks must each see their own trace, never the other's."""

    captured: dict[str, str | None] = {}

    async def worker(name: str, traceparent: str) -> None:
        set_current_trace(traceparent)
        # Yield to the loop so the other task gets a chance to set its own.
        await asyncio.sleep(0)
        captured[name] = get_current_trace()

    async def main() -> None:
        a = new_trace().traceparent
        b = new_trace().traceparent
        await asyncio.gather(worker("a", a), worker("b", b))
        # Each task sees its own value, not the other's.
        assert captured["a"] == a
        assert captured["b"] == b
        assert captured["a"] != captured["b"]

    asyncio.run(main())


def test_get_current_trace_id_returns_trace_id_segment() -> None:
    ids = new_trace()
    set_current_trace(ids.traceparent)
    assert get_current_trace_id() == ids.trace_id


def test_get_current_trace_returns_none_when_unset() -> None:
    async def _runner() -> tuple[str | None, str | None]:
        return get_current_trace(), get_current_trace_id()

    tp, tid = asyncio.run(_runner())
    assert tp is None
    assert tid is None
