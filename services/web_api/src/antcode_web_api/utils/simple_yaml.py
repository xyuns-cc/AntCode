from __future__ import annotations

import json
from typing import Any


def parse_simple_yaml(raw_text: str) -> Any:
    lines = _normalize_lines(raw_text)
    if not lines:
        raise ValueError("导入内容为空")

    data, idx = _parse_block(lines, 0, 0)
    if idx != len(lines):
        raise ValueError("YAML 内容格式不完整")
    return data


def _normalize_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.replace("\t", "  ").rstrip()
        if not line.strip():
            continue
        lines.append(line)
    return lines


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: list[str], start: int, indent: int) -> tuple[Any, int]:
    if start >= len(lines):
        return None, start
    if _indent(lines[start]) < indent:
        return None, start
    if _is_list_item(lines[start], indent):
        return _parse_list(lines, start, indent)
    return _parse_dict(lines, start, indent)


def _is_list_item(line: str, indent: int) -> bool:
    if _indent(line) != indent:
        return False
    stripped = line.strip()
    return stripped == "-" or stripped.startswith("- ")


def _parse_list(lines: list[str], start: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    idx = start
    while idx < len(lines):
        line = lines[idx]
        cur_indent = _indent(line)
        if cur_indent < indent:
            break
        if cur_indent != indent:
            raise ValueError("YAML 缩进不一致")
        stripped = line.strip()
        if not (stripped == "-" or stripped.startswith("- ")):
            break
        value_part = stripped[1:].strip()
        if value_part == "":
            item, idx = _parse_block(lines, idx + 1, indent + 2)
            items.append(item)
        else:
            items.append(_parse_scalar(value_part))
            idx += 1
    return items, idx


def _parse_dict(lines: list[str], start: int, indent: int) -> tuple[dict[str, Any], int]:
    obj: dict[str, Any] = {}
    idx = start
    while idx < len(lines):
        line = lines[idx]
        cur_indent = _indent(line)
        if cur_indent < indent:
            break
        if cur_indent != indent:
            raise ValueError("YAML 缩进不一致")
        stripped = line.strip()
        if stripped.startswith("-"):
            break
        key, sep, value_part = stripped.partition(":")
        if not sep:
            raise ValueError("YAML 键值格式错误")
        key = key.strip()
        value_part = value_part.strip()
        if value_part == "":
            value, idx = _parse_block(lines, idx + 1, indent + 2)
            obj[key] = value
        else:
            obj[key] = _parse_scalar(value_part)
            idx += 1
    return obj, idx


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "null":
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip('"')
    if value.startswith("'") and value.endswith("'"):
        return value.strip("'")
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
