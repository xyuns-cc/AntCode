"""Prepare an ephemeral production-Compose release E2E environment from GHCR digests."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from scripts.release_e2e_environment import ReleaseE2ESettings, runner_environment, write_environment

APP_SERVICES = ("web-api", "master", "gateway", "worker", "frontend")
RUNTIME_SERVICES = ("postgres", "redis", "reverse-proxy")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
IMAGE_PATTERN = re.compile(r"\S+@sha256:[0-9a-f]{64}")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
WORKER_SLUG = "release-e2e"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--digest-dir", type=Path, required=True)
    parser.add_argument("--runtime-lock", type=Path, required=True)
    parser.add_argument("--image-prefix", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--repository", required=True)
    return parser.parse_args()


def _validated_digest(value: str, source: str) -> str:
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid release digest: {source}")
    return value


def _digest(path: Path) -> str:
    return _validated_digest(path.read_text(encoding="utf-8").strip(), str(path))


def _runtime_images(lock_path: Path) -> dict[str, str]:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime image lock has an invalid schema")
    images: object = payload.get("images")
    if payload.get("schema_version") != 1 or not isinstance(images, dict) or set(images) != set(RUNTIME_SERVICES):
        raise ValueError("runtime image lock has an invalid schema")
    for name in RUNTIME_SERVICES:
        reference = images[name]
        if not isinstance(reference, str) or IMAGE_PATTERN.fullmatch(reference) is None:
            raise ValueError(f"runtime image is not digest locked: {name}")
    return {name: images[name] for name in RUNTIME_SERVICES}


def _application_images(args: argparse.Namespace) -> dict[str, str]:
    expected = {f"{service}.{suffix}" for service in APP_SERVICES for suffix in ("digest", "platforms.json")}
    actual = {entry.name for entry in args.digest_dir.iterdir()}
    if actual != expected:
        raise ValueError("release digest artifact set is not the exact five-service descriptor set")
    images: dict[str, str] = {}
    for service in APP_SERVICES:
        digest = _digest(args.digest_dir / f"{service}.digest")
        platforms = json.loads((args.digest_dir / f"{service}.platforms.json").read_text(encoding="utf-8"))
        if (
            not isinstance(platforms, dict)
            or set(platforms) != {"schema_version", "index", "amd64", "arm64"}
            or platforms["schema_version"] != 1
        ):
            raise ValueError(f"invalid platform digest descriptor: {service}")
        resolved = {
            name: _validated_digest(str(platforms[name]), f"{service}.{name}") for name in ("index", "amd64", "arm64")
        }
        if resolved["index"] != digest or resolved["amd64"] == resolved["arm64"]:
            raise ValueError(f"inconsistent platform digest descriptor: {service}")
        images[service] = f"{args.image_prefix}-{service}@{digest}"
    return images


def _export_runner_environment(settings: ReleaseE2ESettings, values: dict[str, str]) -> None:
    github_env = os.environ.get("GITHUB_ENV")
    if not github_env:
        return
    exports = {
        "RELEASE_E2E_DIR": str(settings.root),
        "RELEASE_E2E_ENV_FILE": str(settings.root / "production.env"),
        **runner_environment(settings, values),
    }
    for secret in (values["default_admin_password"], values["runner_redis_url"]):
        print(f"::add-mask::{secret}")
    with Path(github_env).open("a", encoding="utf-8") as handle:
        for name, value in exports.items():
            handle.write(f"{name}={value}\n")


def main() -> None:
    args = _arguments()
    if REVISION_PATTERN.fullmatch(args.revision) is None:
        raise ValueError("release revision must be a full lowercase Git commit SHA")
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    settings = ReleaseE2ESettings(
        root=root,
        source_url=f"https://github.com/{args.repository}.git",
        source_ref=args.revision,
        worker_slug=WORKER_SLUG,
    )
    images = {**_application_images(args), **_runtime_images(args.runtime_lock)}
    values = write_environment(settings, images)
    _export_runner_environment(settings, values)


if __name__ == "__main__":
    main()
