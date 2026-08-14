"""Prepare a production-Compose Gateway E2E environment from locally built images.

与 `prepare_release_e2e.py` 的唯一差别是镜像来源：这里的五个应用镜像来自本机
构建（`infra/docker/run-gateway-e2e.sh`），而不是 GHCR 上的 release digest
artifacts——测试机/内网机器拿不到那份产物。Compose 拓扑、安全画像、mTLS PKI、
密钥与网络布局全部走 `release_e2e_environment`，与发布流水线同一份实现。

前提：Docker 必须启用 containerd 镜像存储，本机构建的镜像才会带 RepoDigests。
没有 digest 就无法按 `repo@sha256:` pin，生产 Compose 会直接缺变量而失败——
这里 fail-closed 报错，不退化成 tag 引用。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from scripts.prepare_release_e2e import APP_SERVICES, _runtime_images
from scripts.release_e2e_environment import ReleaseE2ESettings, runner_environment, write_environment

IMAGE_PATTERN = re.compile(r"\S+@sha256:[0-9a-f]{64}")
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
WORKER_SLUG = "local-gateway-e2e"
IMAGE_NAMESPACE = "antcode"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime-lock", type=Path, required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--runner-env", type=Path, required=True)
    parser.add_argument("--https-port", type=int, required=True)
    parser.add_argument("--http-redirect-port", type=int, required=True)
    parser.add_argument("--gateway-port", type=int, required=True)
    return parser.parse_args()


def _local_digest_reference(service: str, tag: str) -> str:
    """按 tag 查出本机镜像的 RepoDigest，供生产 Compose 按 digest pin。"""
    image = f"{IMAGE_NAMESPACE}-{service}:{tag}"
    completed = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
        check=True,
        text=True,
        capture_output=True,
    )
    digests = json.loads(completed.stdout)
    if not isinstance(digests, list) or len(digests) != 1:
        raise RuntimeError(f"本机镜像没有唯一 RepoDigest（需要 containerd 镜像存储）: {image}")
    reference = str(digests[0])
    if IMAGE_PATTERN.fullmatch(reference) is None:
        raise RuntimeError(f"本机镜像 RepoDigest 不是规范 digest 引用: {image}")
    return reference


def _application_images(tag: str) -> dict[str, str]:
    return {service: _local_digest_reference(service, tag) for service in APP_SERVICES}


def main() -> None:
    args = _arguments()
    if REVISION_PATTERN.fullmatch(args.source_ref) is None:
        raise ValueError("--source-ref 必须是完整的 40 位小写 Git commit SHA")
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    settings = ReleaseE2ESettings(
        root=root,
        source_url=args.source_url,
        source_ref=args.source_ref,
        worker_slug=WORKER_SLUG,
        gateway_public_port=args.gateway_port,
        https_port=args.https_port,
        http_redirect_port=args.http_redirect_port,
    )
    images = {**_application_images(args.image_tag), **_runtime_images(args.runtime_lock)}
    values = write_environment(settings, images)
    exports = runner_environment(settings, values)
    lines = "".join(f"{name}={value}\n" for name, value in sorted(exports.items()))
    args.runner_env.write_text(lines, encoding="utf-8")
    args.runner_env.chmod(0o600)


if __name__ == "__main__":
    main()
