"""Trust policy for untrusted Node project dependency metadata."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from antcode_core.common.config import settings
from loguru import logger

from antcode_worker.runtime.dependency_process import DependencyLimits, run_dependency_command
from antcode_worker.runtime.node_dependency_errors import (
    NODE_DEP_INSTALL_FAILED,
    NODE_DEP_LOCKFILE_MISSING,
    NODE_DEP_LOCKFILE_REJECTED,
    NODE_DEP_MANIFEST_REJECTED,
    NODE_DEP_OFFLINE_CACHE_MISSING,
    NODE_DEP_PACKAGE_MANAGER_UNSUPPORTED,
    NODE_DEP_UNTRUSTED_CONFIG,
    NodeDependencyInstallError,
    NodeDependencyRejected,
)
from antcode_worker.runtime.node_lockfile_policy import allowed_registries, validate_lock_data

_PACKAGE_NAME_PATTERN = re.compile(r"^(?:@[a-z0-9._-]+/)?[a-z0-9._-]+$", re.IGNORECASE)
_PACKAGE_SPEC_PATTERN = re.compile(r"^[A-Za-z0-9*^~<>=| ._-]+$")
_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
_FORBIDDEN_CONFIG_FILES = (
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnpmfile.cjs",
    "pnpm-workspace.yaml",
)
_NODE_INSTALL_ENV_KEYS = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
_OFFLINE_CACHE_ROOT = ".antcode-deps"
_NPM_CACHE_DIR = "npm-cache"
# Worker 镜像只装了 node（自带 npm/npx），没有 pnpm/yarn，见 infra/docker/Dockerfile.worker。
# 装配期能用的包管理器就这一个，校验期必须按这个事实拒绝，不许放行到执行期再炸。
_SUPPORTED_LOCKFILE = "package-lock.json"
_UNSUPPORTED_LOCKFILES = ("pnpm-lock.yaml", "yarn.lock")


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
        raise NodeDependencyInstallError(
            NODE_DEP_INSTALL_FAILED,
            f"Node 依赖装配失败（{' '.join(command[:2])}）: {result.stderr or result.stdout}",
        )


def _node_install_command(root: Path, registry: str) -> list[str] | None:
    if (root / "node_modules").is_dir() or not _has_declared_dependencies(root):
        return None
    if not (root / _SUPPORTED_LOCKFILE).is_file():
        raise NodeDependencyInstallError(
            NODE_DEP_LOCKFILE_MISSING,
            f"Node 外部依赖必须提供 lockfile 和离线缓存：把 {_SUPPORTED_LOCKFILE} 与 "
            f"{_OFFLINE_CACHE_ROOT}/{_NPM_CACHE_DIR}/ 一起提交进仓库",
        )
    cache = _require_offline_cache(root, _NPM_CACHE_DIR)
    return [
        "npm",
        "ci",
        "--cache",
        str(cache),
        "--no-audit",
        "--no-fund",
        "--offline",
        "--ignore-scripts",
        "--registry",
        registry,
    ]


def _has_declared_dependencies(root: Path) -> bool:
    package_data = _load_json(root / "package.json")
    return any(bool(package_data.get(section)) for section in _DEPENDENCY_SECTIONS)


def _require_offline_cache(root: Path, directory: str) -> Path:
    cache = root / _OFFLINE_CACHE_ROOT / directory
    if not cache.is_dir():
        raise NodeDependencyInstallError(
            NODE_DEP_OFFLINE_CACHE_MISSING,
            f"Node 外部依赖缺少离线缓存 {_OFFLINE_CACHE_ROOT}/{directory}/：任务沙箱无网络，"
            f"请在本地跑 `npm ci --cache {_OFFLINE_CACHE_ROOT}/{directory}` 后把该目录一起提交",
        )
    resolved = cache.resolve()
    if os.path.commonpath([str(root.resolve()), str(resolved)]) != str(root.resolve()):
        raise NodeDependencyInstallError(
            NODE_DEP_OFFLINE_CACHE_MISSING,
            f"Node 离线缓存路径越出项目工作区: {resolved}",
        )
    return resolved


def validate_node_dependency_metadata(cwd: str) -> str:
    root = Path(cwd)
    _reject_project_package_manager_config(root)
    _reject_unsupported_lockfiles(root)
    package_data = _load_json(root / "package.json")
    _validate_package_dependencies(package_data)
    lockfile = root / _SUPPORTED_LOCKFILE
    if lockfile.is_file():
        validate_lock_data(_load_json(lockfile))
    return allowed_registries()[0]


def _reject_unsupported_lockfiles(root: Path) -> None:
    """镜像里只有 npm，其它包管理器必须在校验期就拒。

    此前 pnpm 是"校验层宣称支持、镜像里根本没装"：``validate`` 放行、装配期才
    以 `任务可执行文件不存在: pnpm` 收场——那句话和 pnpm 支持与否毫无关系。
    """
    present = [name for name in _UNSUPPORTED_LOCKFILES if (root / name).is_file()]
    if not present:
        return
    raise NodeDependencyRejected(
        NODE_DEP_PACKAGE_MANAGER_UNSUPPORTED,
        f"Worker 镜像只提供 npm，不支持 {', '.join(present)}；"
        f"请改用 {_SUPPORTED_LOCKFILE} 并提交 {_OFFLINE_CACHE_ROOT}/{_NPM_CACHE_DIR}/ 离线缓存",
    )


def _reject_project_package_manager_config(root: Path) -> None:
    present = [name for name in _FORBIDDEN_CONFIG_FILES if (root / name).exists()]
    if present:
        raise NodeDependencyRejected(
            NODE_DEP_UNTRUSTED_CONFIG,
            f"Node 项目包含不受信任的包管理器配置: {', '.join(present)}",
        )


def _load_json(path: Path) -> Any:
    content = _read_bounded(path)
    try:
        return json.loads(content, parse_constant=lambda value: _reject_json_constant(value))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise NodeDependencyRejected(
            NODE_DEP_LOCKFILE_REJECTED,
            f"Node 依赖文件不是有效 JSON: {path.name}",
        ) from exc


def _reject_json_constant(value: str):
    raise NodeDependencyRejected(NODE_DEP_LOCKFILE_REJECTED, f"Node 依赖 JSON 不允许常量 {value}")


def _read_bounded(path: Path) -> str:
    size = path.stat().st_size
    if size > settings.NODE_LOCKFILE_MAX_BYTES:
        raise NodeDependencyRejected(
            NODE_DEP_LOCKFILE_REJECTED,
            f"Node 依赖文件超过上限 {settings.NODE_LOCKFILE_MAX_BYTES} 字节: {path.name}",
        )
    return path.read_text(encoding="utf-8")


def _validate_package_dependencies(package_data: Any) -> None:
    if not isinstance(package_data, dict):
        raise NodeDependencyRejected(NODE_DEP_MANIFEST_REJECTED, "package.json 必须是对象")
    for section in _DEPENDENCY_SECTIONS:
        dependencies = package_data.get(section, {})
        if not isinstance(dependencies, dict):
            raise NodeDependencyRejected(NODE_DEP_MANIFEST_REJECTED, f"package.json {section} 必须是对象")
        _validate_dependency_section(dependencies)


def _validate_dependency_section(dependencies: dict[Any, Any]) -> None:
    for name, specifier in dependencies.items():
        if not isinstance(name, str) or not _PACKAGE_NAME_PATTERN.fullmatch(name):
            raise NodeDependencyRejected(NODE_DEP_MANIFEST_REJECTED, f"Node 包名不合法: {name!r}")
        if not isinstance(specifier, str) or not _PACKAGE_SPEC_PATTERN.fullmatch(specifier):
            raise NodeDependencyRejected(
                NODE_DEP_MANIFEST_REJECTED,
                f"Node 包版本仅允许 registry semver/tag: {name}",
            )


__all__ = ["install_node_dependencies", "validate_node_dependency_metadata"]
