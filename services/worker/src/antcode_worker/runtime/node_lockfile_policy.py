"""Lockfile 依赖来源判定：哪些标量是"包从哪儿下载"，哪些只是元数据。

从 ``node_dependency_policy`` 拆出来的原因是文件超了 300 行硬上限；这里只管
"lockfile 里的一个字符串该不该被当成依赖来源校验"，装配编排留在原模块。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from antcode_core.common.config import settings

from antcode_worker.runtime.node_dependency_errors import (
    NODE_DEP_LOCKFILE_REJECTED,
    NODE_DEP_REGISTRY_MISCONFIGURED,
    NODE_DEP_REGISTRY_REJECTED,
    NODE_DEP_SOURCE_REJECTED,
    NodeDependencyInstallError,
    NodeDependencyRejected,
)

_LOCK_SOURCE_KEYS = {"path", "resolved", "specifier", "tarball", "version"}
# npm 会把两类自由文本原样写进 lockfile，它们都**不是**包的下载来源：
#   * ``funding`` / ``funding.url``：作者赞助页（``https://opencollective.com/eslint``）
#   * ``deprecated``：维护者留给人看的整句话，常带 issue 链接
# 旧实现对"任何含 :// 的标量"一律套 registry 白名单，于是 `npm i -D tsx` 生成的原始
# lockfile 直接被拒——白名单该管的是"包从哪儿下载"，不是"赞助页在哪儿"。
# 实测依据：191 个包的真实 lockfile 里，带 URL 的 key 只有 resolved / url / deprecated。
#
# 只豁免"叶子标量 + 其直接父键"这一对，不做整棵子树剪枝：lockfile v1 的
# ``dependencies`` 是 name→entry，而 npm 上真存在名为 url / funding / deprecated 的
# 包，剪子树会让这些包自己的 ``resolved`` 逃掉检查。任何未登记的 key 仍然 fail-closed。
_LOCK_METADATA_KEYS = frozenset({"deprecated", "funding"})
_LOCK_METADATA_CHILD_KEYS = frozenset({("funding", "url")})
_FORBIDDEN_SOURCE_PREFIXES = (
    "file:",
    "git:",
    "git+",
    "github:",
    "http:",
    "https:",
    "link:",
    "ssh:",
    "workspace:",
)
_MAX_LOCKFILE_NODES = 200_000
_MAX_LOCKFILE_DEPTH = 100


def validate_lock_data(value: Any) -> None:
    stack = [(value, "", "", 1)]
    node_count = 0
    while stack:
        node, key, parent_key, depth = stack.pop()
        node_count += 1
        _require_within_bounds(node_count, depth)
        if isinstance(node, dict):
            stack.extend((child, str(child_key), key, depth + 1) for child_key, child in node.items())
        elif isinstance(node, list):
            # 列表元素沿用父键：``funding: [{...}]`` 与 ``funding: {...}`` 必须同一判定。
            stack.extend((child, key, parent_key, depth + 1) for child in node)
        elif isinstance(node, str):
            _validate_lock_scalar(node, key, parent_key)


def _require_within_bounds(node_count: int, depth: int) -> None:
    if node_count > _MAX_LOCKFILE_NODES:
        raise NodeDependencyRejected(
            NODE_DEP_LOCKFILE_REJECTED,
            f"Node lockfile 展开节点数超过上限 {_MAX_LOCKFILE_NODES}",
        )
    if depth > _MAX_LOCKFILE_DEPTH:
        raise NodeDependencyRejected(
            NODE_DEP_LOCKFILE_REJECTED,
            f"Node lockfile 展开深度超过上限 {_MAX_LOCKFILE_DEPTH}",
        )


def _validate_lock_scalar(value: str, key: str, parent_key: str) -> None:
    if key in _LOCK_METADATA_KEYS or (parent_key, key) in _LOCK_METADATA_CHILD_KEYS:
        return
    normalized = value.strip().lower()
    if normalized.startswith(_FORBIDDEN_SOURCE_PREFIXES) or "://" in normalized:
        _validate_lock_source(value)
        return
    if key not in _LOCK_SOURCE_KEYS:
        return
    if key in {"resolved", "tarball"}:
        _validate_registry_url(value)
        return
    if key == "path" and (normalized.startswith((".", "/", "\\")) or ".." in Path(normalized).parts):
        raise NodeDependencyRejected(NODE_DEP_SOURCE_REJECTED, f"Node lockfile 包含禁止的本地路径: {value}")
    _validate_lock_source(value)


def _validate_lock_source(value: str) -> None:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered.startswith(_FORBIDDEN_SOURCE_PREFIXES):
        if lowered.startswith("https:"):
            _validate_registry_url(normalized)
            return
        raise NodeDependencyRejected(NODE_DEP_SOURCE_REJECTED, f"Node lockfile 包含禁止的依赖来源: {normalized}")
    if "://" in normalized:
        _validate_registry_url(normalized)


def _validate_registry_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise NodeDependencyRejected(NODE_DEP_SOURCE_REJECTED, f"Node lockfile 依赖 URL 不安全: {value}")
    normalized = value.rstrip("/")
    if not any(normalized.startswith(registry.rstrip("/") + "/") for registry in allowed_registries()):
        raise NodeDependencyRejected(
            NODE_DEP_REGISTRY_REJECTED,
            f"Node lockfile 依赖 URL 不在允许 registry: {value}",
        )


def allowed_registries() -> tuple[str, ...]:
    registries = tuple(
        item.strip().rstrip("/") for item in settings.NODE_PACKAGE_REGISTRY_ALLOWLIST.split(",") if item.strip()
    )
    if not registries:
        raise NodeDependencyInstallError(NODE_DEP_REGISTRY_MISCONFIGURED, "NODE_PACKAGE_REGISTRY_ALLOWLIST 不能为空")
    for registry in registries:
        parsed = urlsplit(registry)
        has_credentials = any((parsed.username, parsed.password, parsed.query, parsed.fragment))
        if parsed.scheme != "https" or not parsed.hostname or has_credentials:
            raise NodeDependencyInstallError(NODE_DEP_REGISTRY_MISCONFIGURED, f"Node registry 配置不安全: {registry}")
    return registries


__all__ = ["allowed_registries", "validate_lock_data"]
