"""Strict YAML parsing for untrusted import files."""

from __future__ import annotations

from typing import Any

import yaml  # type: ignore[import-untyped]
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent  # type: ignore[import-untyped]

MAX_YAML_IMPORT_NODES = 10_000
MAX_YAML_IMPORT_DEPTH = 50
MAX_YAML_EXPANDED_SCALAR_BYTES = 2 * 1024 * 1024


def load_untrusted_yaml(raw_text: str, *, max_input_bytes: int) -> Any:
    encoded_size = len(raw_text.encode("utf-8"))
    if encoded_size > max_input_bytes:
        raise ValueError(f"YAML 输入超过上限 {max_input_bytes} 字节")
    _validate_yaml_events(raw_text)
    value = yaml.safe_load(raw_text)
    _validate_loaded_graph(value)
    return value


def _validate_yaml_events(raw_text: str) -> None:
    depth = 0
    event_count = 0
    for event in yaml.parse(raw_text, Loader=yaml.SafeLoader):
        event_count += 1
        if event_count > (MAX_YAML_IMPORT_NODES * 2) + 4:
            raise ValueError(f"YAML 节点数超过上限 {MAX_YAML_IMPORT_NODES}")
        if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
            raise ValueError("YAML 导入不允许 anchor 或 alias")
        if isinstance(event, CollectionStartEvent):
            depth += 1
            if depth > MAX_YAML_IMPORT_DEPTH:
                raise ValueError(f"YAML 嵌套深度超过上限 {MAX_YAML_IMPORT_DEPTH}")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1


def _validate_loaded_graph(value: Any) -> None:
    stack = [(value, 1)]
    container_ids: set[int] = set()
    node_count = 0
    scalar_bytes = 0
    while stack:
        node, depth = stack.pop()
        node_count += 1
        if node_count > MAX_YAML_IMPORT_NODES:
            raise ValueError(f"YAML 展开节点数超过上限 {MAX_YAML_IMPORT_NODES}")
        if depth > MAX_YAML_IMPORT_DEPTH:
            raise ValueError(f"YAML 展开深度超过上限 {MAX_YAML_IMPORT_DEPTH}")
        children = _node_children(node)
        if children is not None:
            identity = id(node)
            if identity in container_ids:
                raise ValueError("YAML 导入不允许共享或循环引用")
            container_ids.add(identity)
            stack.extend((child, depth + 1) for child in children)
            continue
        scalar_bytes += len(str(node).encode("utf-8"))
        if scalar_bytes > MAX_YAML_EXPANDED_SCALAR_BYTES:
            raise ValueError(f"YAML 展开标量超过上限 {MAX_YAML_EXPANDED_SCALAR_BYTES} 字节")


def _node_children(node: Any) -> list[Any] | None:
    if isinstance(node, dict):
        children: list[Any] = []
        for key, value in node.items():
            children.extend((key, value))
        return children
    if isinstance(node, (list, tuple, set)):
        return list(node)
    return None


__all__ = [
    "MAX_YAML_EXPANDED_SCALAR_BYTES",
    "MAX_YAML_IMPORT_DEPTH",
    "MAX_YAML_IMPORT_NODES",
    "load_untrusted_yaml",
]
