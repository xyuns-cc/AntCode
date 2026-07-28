"""Shared package-manager cache environment wiring."""

from __future__ import annotations

import os

from antcode_core.common.config import settings


def inject_language_cache_env(env: dict[str, str]) -> None:
    cache_root = settings.LANG_CACHE_ROOT
    node_cache = os.path.join(cache_root, "node")
    pnpm_store = os.path.join(cache_root, "pnpm-store")
    go_path = os.path.join(cache_root, "go")
    go_cache = os.path.join(cache_root, "go-build")
    m2_repo = os.path.join(cache_root, "m2")
    for directory in (node_cache, pnpm_store, go_path, go_cache, m2_repo):
        os.makedirs(directory, exist_ok=True)
    env.setdefault("npm_config_cache", node_cache)
    env.setdefault("PNPM_STORE_PATH", pnpm_store)
    env.setdefault("GOPATH", go_path)
    env.setdefault("GOCACHE", go_cache)
    maven_opts = env.get("MAVEN_OPTS", os.environ.get("MAVEN_OPTS", ""))
    if "-Dmaven.repo.local" not in maven_opts:
        addition = f"-Dmaven.repo.local={m2_repo}"
        env["MAVEN_OPTS"] = f"{maven_opts} {addition}".strip() if maven_opts else addition


__all__ = ["inject_language_cache_env"]
