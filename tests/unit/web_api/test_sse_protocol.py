"""Strict JSON and stable ID rules for browser-facing SSE frames."""

import pytest
from antcode_web_api.streams.sse import format_sse_event


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_sse_frame_rejects_non_json_floats(value: float) -> None:
    with pytest.raises(ValueError, match="Out of range float"):
        format_sse_event("run_status", {"progress": value})


def test_sse_frame_rejects_unknown_objects_instead_of_stringifying() -> None:
    with pytest.raises(TypeError, match="JSON serializable"):
        format_sse_event("log_line", {"data": object()})


@pytest.mark.parametrize("event_id", ["1-0:0\nnext", "1-0:0\rnext"])
def test_sse_frame_rejects_multiline_event_id(event_id: str) -> None:
    with pytest.raises(ValueError, match="不得包含换行"):
        format_sse_event("log_line", {}, event_id=event_id)
