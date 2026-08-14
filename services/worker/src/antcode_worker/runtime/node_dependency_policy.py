"""Trust policy for untrusted Node project dependency metadata."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml  # type: ignore[import-untyped]
from antcode_core.common.config import settings
from loguru import logger
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent  # type: ignore[import-untyped]

from antcode_worker.runtime.dependency_process import DependencyLimits, run_dependency_command

_PACKAGE_NAME_PATTERN = re.compile(r"^(?:@[a-z0-9._-]+/)?[a-z0-9._-]+$", re.IGNORECASE)
_PACKAGE_SPEC_PATTERN = re.compile(r"^[A-Za-z0-9*^~<>=| ._-]+$")
_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_LOCK_SOURCE_KEYS = {"path", "resolved", "specifier", "tarball", "version"}
_FORBIDDEN_CONFIG_FILES = (
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnpmfile.cjs",
    "pnpm-workspace.yaml",
)
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
_NODE_INSTALL_ENV_KEYS = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
_MAX_LOCKFILE_NODES = 200_000
_MAX_LOCKFILE_DEPTH = 100
_OFFLINE_CACHE_ROOT = ".antcode-deps"
_NPM_CACHE_DIR = "npm-cache"
_PNPM_STORE_DIR = "pnpm-store"


async def install_node_dependencies(cwd: str, *, limits: DependencyLimits) -> None:
    root = Path(cwd)
    if not (root / "package.json").is_file():
        return
    registry = validate_node_dependency_metadata(cwd)
    command = _node_install_command(root, registry)
    if command is None:
        logger.debug("Node 项目无需离线依赖装配: {}", cwd)
        return
    install_env = {key: os.environ[key] for key in _NODE_INSTALL_ENV_KEYS if key in os.environ}
    install_env.update(
        HOME=cwd,
        npm_config_ignore_scripts="true",
        npm_config_offline="true",
        npm_config_userconfig=os.devnull,
        npm_config_registry=registry,
    )
    result = await run_dependency_command(
        command,
        cwd=cwd,
        env=install_env,
        limits=limits,
    )
    if result.exit_code != 0:
        raise RuntimeError(f"Node 依赖装配失败: {result.stderr or result.stdout}")


def _node_install_command(root: Path, registry: str) -> list[str] | None:
    if (root / "node_modules").is_dir() or not _has_declared_dependencies(root):
        return None
    common = ["--offline", "--ignore-scripts", "--registry", registry]
    if (root / "pnpm-lock.yaml").is_file():
        store = _require_offline_cache(root, _PNPM_STORE_DIR)
        return ["pnpm", "install", "--frozen-lockfile", "--store-dir", str(store), *common]
    if (root / "yarn.lock").is_file():
        raise RuntimeError("安全模式不支持自动安装 yarn.lock")
    if (root / "package-lock.json").is_file():
        cache = _require_offline_cache(root, _NPM_CACHE_DIR)
        return ["npm", "ci", "--cache", str(cache), "--no-audit", "--no-fund", *common]
    raise RuntimeError("Node 外部依赖必须提供 lockfile 和 .antcode-deps 离线缓存")


def _has_declared_dependencies(root: Path) -> bool:
    package_data = _load_json(root / "package.json")
    return any(bool(package_data.get(section)) for section in _DEPENDENCY_SECTIONS)


def _require_offline_cache(root: Path, directory: str) -> Path:
    cache = root / _OFFLINE_CACHE_ROOT / directory
    if not cache.is_dir():
        raise RuntimeError(f"Node 外部依赖缺少离线缓存: {_OFFLINE_CACHE_ROOT}/{directory}")
    resolved = cache.resolve()
    if os.path.commonpath([str(root.resolve()), str(resolved)]) != str(root.resolve()):
        raise RuntimeError("Node 离线缓存路径越出项目工作区")
    return resolved


def validate_node_dependency_metadata(cwd: str) -> str:
    root = Path(cwd)
    _reject_project_package_manager_config(root)
    package_data = _load_json(root / "package.json")
    _validate_package_dependencies(package_data)
    if (root / "yarn.lock").is_file():
        raise ValueError("安全模式不支持自动安装 yarn.lock，请使用 package-lock.json 或 pnpm-lock.yaml")
    if (root / "package-lock.json").is_file():
        _validate_lock_data(_load_json(root / "package-lock.json"))
    elif (root / "pnpm-lock.yaml").is_file():
        _validate_lock_data(_load_pnpm_lock(root / "pnpm-lock.yaml"))
    return _allowed_registries()[0]


def _reject_project_package_manager_config(root: Path) -> None:
    present = [name for name in _FORBIDDEN_CONFIG_FILES if (root / name).exists()]
    if present:
        raise ValueError(f"Node 项目包含不受信任的包管理器配置: {', '.join(present)}")


def _load_json(path: Path) -> Any:
    content = _read_bounded(path)
    try:
        return json.loads(content, parse_constant=lambda value: _reject_json_constant(value))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"Node 依赖文件不是有效 JSON: {path.name}") from exc


def _reject_json_constant(value: str):
    raise ValueError(f"Node 依赖 JSON 不允许常量 {value}")


def _load_pnpm_lock(path: Path) -> Any:
    content = _read_bounded(path)
    event_count = 0
    depth = 0
    for event in yaml.parse(content, Loader=yaml.SafeLoader):
        event_count += 1
        if event_count > (_MAX_LOCKFILE_NODES * 2) + 4:
            raise ValueError(f"pnpm lockfile 节点数超过上限 {_MAX_LOCKFILE_NODES}")
        if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
            raise ValueError("pnpm lockfile 不允许 YAML anchor 或 alias")
        if isinstance(event, CollectionStartEvent):
            depth += 1
            if depth > _MAX_LOCKFILE_DEPTH:
                raise ValueError(f"pnpm lockfile 深度超过上限 {_MAX_LOCKFILE_DEPTH}")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1
    return yaml.safe_load(content)


def _read_bounded(path: Path) -> str:
    size = path.stat().st_size
    if size > settings.NODE_LOCKFILE_MAX_BYTES:
        raise ValueError(f"Node 依赖文件超过上限 {settings.NODE_LOCKFILE_MAX_BYTES} 字节: {path.name}")
    return path.read_text(encoding="utf-8")


def _validate_package_dependencies(package_data: Any) -> None:
    if not isinstance(package_data, dict):
        raise ValueError("package.json 必须是对象")
    for section in _DEPENDENCY_SECTIONS:
        dependencies = package_data.get(section, {})
        if not isinstance(dependencies, dict):
            raise ValueError(f"package.json {section} 必须是对象")
        for name, specifier in dependencies.items():
            if not isinstance(name, str) or not _PACKAGE_NAME_PATTERN.fullmatch(name):
                raise ValueError(f"Node 包名不合法: {name!r}")
            if not isinstance(specifier, str) or not _PACKAGE_SPEC_PATTERN.fullmatch(specifier):
                raise ValueError(f"Node 包版本仅允许 registry semver/tag: {name}")


def _validate_lock_data(value: Any) -> None:
    stack = [(value, "", 1)]
    node_count = 0
    while stack:
        node, key, depth = stack.pop()
        node_count += 1
        if node_count > _MAX_LOCKFILE_NODES:
            raise ValueError(f"Node lockfile 展开节点数超过上限 {_MAX_LOCKFILE_NODES}")
        if depth > _MAX_LOCKFILE_DEPTH:
            raise ValueError(f"Node lockfile 展开深度超过上限 {_MAX_LOCKFILE_DEPTH}")
        if isinstance(node, dict):
            stack.extend((child, str(child_key), depth + 1) for child_key, child in node.items())
        elif isinstance(node, list):
            stack.extend((child, key, depth + 1) for child in node)
        elif isinstance(node, str):
            _validate_lock_scalar(node, key)


def _validate_lock_scalar(value: str, key: str) -> None:
    normalized = value.strip().lower()
    if normalized.startswith(_FORBIDDEN_SOURCE_PREFIXES) or "://" in normalized:
        _validate_lock_source(value)
        return
    if key in _LOCK_SOURCE_KEYS:
        if key in {"resolved", "tarball"}:
            _validate_registry_url(value)
            return
        if key == "path" and (normalized.startswith((".", "/", "\\")) or ".." in Path(normalized).parts):
            raise ValueError(f"Node lockfile 包含禁止的本地路径: {value}")
        _validate_lock_source(value)


def _validate_lock_source(value: str) -> None:
    normalized = value.strip()
    lowered = normalized.lower()
    if lowered.startswith(_FORBIDDEN_SOURCE_PREFIXES):
        if lowered.startswith("https:"):
            _validate_registry_url(normalized)
            return
        raise ValueError(f"Node lockfile 包含禁止的依赖来源: {normalized}")
    if "://" in normalized:
        _validate_registry_url(normalized)


def _validate_registry_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError(f"Node lockfile 依赖 URL 不安全: {value}")
    normalized = value.rstrip("/")
    if not any(normalized.startswith(registry.rstrip("/") + "/") for registry in _allowed_registries()):
        raise ValueError(f"Node lockfile 依赖 URL 不在允许 registry: {value}")


def _allowed_registries() -> tuple[str, ...]:
    registries = tuple(
        item.strip().rstrip("/") for item in settings.NODE_PACKAGE_REGISTRY_ALLOWLIST.split(",") if item.strip()
    )
    if not registries:
        raise RuntimeError("NODE_PACKAGE_REGISTRY_ALLOWLIST 不能为空")
    for registry in registries:
        parsed = urlsplit(registry)
        has_credentials = any((parsed.username, parsed.password, parsed.query, parsed.fragment))
        if parsed.scheme != "https" or not parsed.hostname or has_credentials:
            raise RuntimeError(f"Node registry 配置不安全: {registry}")
    return registries


__all__ = ["install_node_dependencies", "validate_node_dependency_metadata"]
