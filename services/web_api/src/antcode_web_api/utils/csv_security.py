"""CSV output helpers that prevent spreadsheet formula execution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@")
_BYTE_ORDER_MARK = "\ufeff"


def sanitize_csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    candidate = value
    while candidate and (candidate[0].isspace() or candidate[0] == _BYTE_ORDER_MARK):
        candidate = candidate[1:]
    if candidate.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def sanitize_csv_row(values: Iterable[Any]) -> list[Any]:
    return [sanitize_csv_cell(value) for value in values]


__all__ = ["sanitize_csv_cell", "sanitize_csv_row"]
