"""Shared package-manager cache environment wiring."""

from __future__ import annotations

import os

from antcode_core.common.config import settings


def inject_language_cache_env(env: dict[str, str]) -> None:
    cache_root = settings.LANG_CACHE_ROOT
    node_cache = os.path.join(cache_root, "node")
    go_path = os.path.join(cache_root, "go")
    go_cache = os.path.join(cache_root, "go-build")
    m2_repo = os.path.join(cache_root, "m2")
    # 不再准备 pnpm store：镜像里没有 pnpm，node_dependency_policy 已在校验期
    # 显式拒绝 pnpm-lock.yaml，留一个永远没人写的目录只会让人以为支持。
    for directory in (node_cache, go_path, go_cache, m2_repo):
        os.makedirs(directory, exist_ok=True)
    env.setdefault("npm_config_cache", node_cache)
    env.setdefault("GOPATH", go_path)
    env.setdefault("GOCACHE", go_cache)
    maven_opts = env.get("MAVEN_OPTS", os.environ.get("MAVEN_OPTS", ""))
    if "-Dmaven.repo.local" not in maven_opts:
        addition = f"-Dmaven.repo.local={m2_repo}"
        env["MAVEN_OPTS"] = f"{maven_opts} {addition}".strip() if maven_opts else addition


__all__ = ["inject_language_cache_env"]
