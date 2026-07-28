"""Strict SpiderData JSON boundary tests."""

import pytest
from antcode_core.spider_ingest import validate_spider_json


def test_json_byte_limit_is_checked_before_parsing() -> None:
    with pytest.raises(ValueError, match="编码大小超限"):
        validate_spider_json(b"{broken", 1)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_json_constants_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="严格合法 JSON"):
        validate_spider_json(value, 64)


def test_deep_json_recursion_is_reported_as_validation_error() -> None:
    value = "[" * 100_000 + "]" * 100_000

    with pytest.raises(ValueError, match="严格合法 JSON"):
        validate_spider_json(value, len(value))


def test_valid_utf8_json_is_accepted() -> None:
    validate_spider_json('{"title":"Example"}', 64)
