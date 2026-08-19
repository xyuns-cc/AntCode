"""Prepare a production-Compose Gateway E2E environment from Compose-built images.

五个应用镜像由 `infra/docker/run-gateway-e2e.sh` 用 `docker compose build` 就地构建，
生产 Compose 按 tag 引用它们；第三方运行时镜像（postgres / redis / 反向代理）仍按
digest pin，锁在 `infra/docker/release-runtime-images.json`。Compose 拓扑、安全画像、
mTLS PKI、密钥与网络布局全部走 `release_e2e_environment`——测试机验的就是部署跑的。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from scripts.release_e2e_environment import (
    ReleaseE2ESettings,
    runner_environment,
    runtime_images,
    write_environment,
)

REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
WORKER_SLUG = "local-gateway-e2e"
DEFAULT_WORKER_COUNT = 1


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
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKER_COUNT)
    return parser.parse_args()


def main() -> None:
    args = _arguments()
    if REVISION_PATTERN.fullmatch(args.source_ref) is None:
        raise ValueError("--source-ref 必须是完整的 40 位小写 Git commit SHA")
    if args.workers < DEFAULT_WORKER_COUNT:
        raise ValueError("--workers 至少为 1")
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
        worker_count=args.workers,
    )
    values = write_environment(settings, args.image_tag, runtime_images(args.runtime_lock))
    exports = runner_environment(settings, values)
    lines = "".join(f"{name}={value}\n" for name, value in sorted(exports.items()))
    args.runner_env.write_text(lines, encoding="utf-8")
    args.runner_env.chmod(0o600)


if __name__ == "__main__":
    main()
