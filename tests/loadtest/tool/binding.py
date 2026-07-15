from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Any

from redis.asyncio import Redis

from .config import LoadSettings, validate_base_url

RedisFactory = Callable[..., Any]


def build_target_binding_value(
    base_url: str,
    confirmation: str,
    *,
    project_id: str | None,
    worker_id: str | None,
) -> str:
    payload = {
        "base_url": validate_base_url(base_url),
        "confirmation": confirmation,
        "project_id": project_id or None,
        "worker_id": worker_id or None,
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def target_binding_value(settings: LoadSettings) -> str:
    return build_target_binding_value(
        settings.base_url,
        settings.confirmation,
        project_id=settings.project_id,
        worker_id=settings.worker_id,
    )


async def verify_redis_target_binding(
    settings: LoadSettings,
    *,
    redis_factory: RedisFactory | None = None,
) -> None:
    factory = redis_factory or Redis.from_url
    client = factory(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.timeout_seconds,
        socket_timeout=settings.timeout_seconds,
    )
    try:
        actual = await client.get(settings.redis_binding_key)
    except Exception as exc:
        raise RuntimeError("load-test Redis binding could not be verified") from exc
    finally:
        await client.aclose()
    if actual != target_binding_value(settings):
        raise RuntimeError(
            "load-test Redis binding mismatch: the configured Redis database is not explicitly bound to this target"
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print the exact AntCode load-test Redis binding marker")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--confirmation", choices=("READ_ONLY", "FULL"), required=True)
    parser.add_argument("--project-id")
    parser.add_argument("--worker-id")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    print(
        build_target_binding_value(
            args.base_url,
            args.confirmation,
            project_id=args.project_id,
            worker_id=args.worker_id,
        )
    )


if __name__ == "__main__":
    main()
