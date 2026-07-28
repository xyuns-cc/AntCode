"""导出用的最小 YAML 序列化（tasks/project 导出共享，避免额外依赖）。

P2 §4.4 / 复审 P3: YAML 1.1 会把裸标量 ``null/true/on/off``、数字形态
字符串等解析成其它类型；字符串一旦会被误读必须加引号。此前 tasks 与
project 各有一份实现且 project 版缺失该防护，统一收敛到这里。
"""

from __future__ import annotations

import json
import re
from typing import Any

_YAML_RESERVED_SCALARS = frozenset({"null", "~", "none", "true", "false", "yes", "no", "on", "off"})
_YAML_NUMERIC_PATTERN = re.compile(r"[+-]?(?:\d[\d_]*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|0x[0-9a-fA-F]+|0o[0-7]+")
_YAML_SPECIAL_CHARS = ":-#\n\"'{}[]&*!|>%@`,?"


def yaml_dump(data: Any, indent: int = 0) -> str:
    """简单 YAML 序列化（仅支持 dict/list/标量的导出场景）。"""
    prefix = "  " * indent
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(yaml_dump(value, indent + 1))
            else:
                lines.append(f"{prefix}{key}: {yaml_scalar(value)}")
        return "\n".join(lines)
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(yaml_dump(item, indent + 1))
            else:
                lines.append(f"{prefix}- {yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{yaml_scalar(data)}"


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if yaml_needs_quoting(text):
        return json.dumps(text, ensure_ascii=False)
    return text


def yaml_needs_quoting(text: str) -> bool:
    """字符串会被 YAML 解析器误读成其它类型/结构时必须引用。"""
    if text == "" or text.strip() != text:
        return True
    if text.lower() in _YAML_RESERVED_SCALARS:
        return True
    if _YAML_NUMERIC_PATTERN.fullmatch(text):
        return True
    return any(c in text for c in _YAML_SPECIAL_CHARS)


__all__ = ["yaml_dump", "yaml_needs_quoting", "yaml_scalar"]
