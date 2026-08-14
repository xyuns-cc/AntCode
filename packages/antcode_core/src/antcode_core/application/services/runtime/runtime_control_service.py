"""运行时管理控制服务"""

from __future__ import annotations

import json
import uuid
from typing import Any

from antcode_contracts.runtime_metadata import validate_runtime_creator, validate_runtime_metadata
from loguru import logger

from antcode_core.common.error_messages import normalize_persisted_error_message
from antcode_core.infrastructure.redis import (
    build_runtime_manage_control_payload,
    control_reply_stream,
    control_stream,
    decode_stream_payload,
    get_redis_client,
    runtime_control_request_id,
)

MILLISECONDS_PER_SECOND = 1000
MICROSECONDS_PER_MILLISECOND = 1000
MAX_RUNTIME_RESULT_BYTES = 1024 * 1024
MAX_RUNTIME_ERROR_BYTES = 16 * 1024


def _decode_runtime_response(decoded: dict[str, Any]) -> dict[str, Any]:
    success_value = str(decoded.get("success", "")).lower()
    if success_value not in {"true", "false"}:
        raise RuntimeError("运行时控制响应 success 字段无效")
    error = normalize_persisted_error_message(decoded.get("error")) or ""
    if len(error.encode("utf-8")) > MAX_RUNTIME_ERROR_BYTES:
        raise RuntimeError("运行时控制响应 error 超过 16 KiB 上限")
    if "data" not in decoded:
        raise RuntimeError("运行时控制响应缺少 data JSON")
    data_obj = decoded["data"]
    if isinstance(data_obj, str) and not data_obj:
        # decode_stream_payload 对空字符串字段宽容跳过（兼容旧写入端）；
        # 运行时控制响应是新协议，空 data 视为响应损坏（无数据必须用 null）。
        raise RuntimeError("运行时控制响应 data 不是合法 JSON")
    serialized = json.dumps(data_obj, ensure_ascii=False, allow_nan=False)
    if len(serialized.encode("utf-8")) > MAX_RUNTIME_RESULT_BYTES:
        raise RuntimeError("运行时控制响应 data 超过 1 MiB 上限")
    return {"success": success_value == "true", "error": error, "data": data_obj}


async def write_control_event(
    redis: Any,
    control_stream_key: str,
    payload: dict[str, Any],
) -> str:
    """Write a control event; successful ACK paths own retention."""
    return await redis.xadd(control_stream_key, payload)


async def redis_server_now_ms(redis: Any) -> int:
    """Redis 服务端时钟（毫秒）。

    复审 P1-DR-04: 运行时控制 deadline 必须以 Redis TIME 为单一时钟权威
    ——Master/Worker 各自的 wall clock 存在偏移，会执行已过期指令或拒绝
    有效指令。与 worker 侧 ``settlement_expiry_ms`` 使用同一时钟。
    """
    redis_time = await redis.time()
    try:
        seconds, microseconds = (int(value) for value in redis_time)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Redis TIME 响应无效") from exc
    return seconds * MILLISECONDS_PER_SECOND + microseconds // MICROSECONDS_PER_MILLISECOND


class RuntimeControlService:
    """运行时管理控制服务"""

    def __init__(self, default_timeout: float = 30.0, reply_ttl: int = 120):
        self._default_timeout = default_timeout
        self._reply_ttl = reply_ttl

    async def send_command(
        self,
        worker_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """发送运行时管理控制指令"""
        timeout_seconds = self._default_timeout if timeout is None else timeout
        if timeout_seconds <= 0:
            raise ValueError("运行时控制 timeout 必须大于 0")
        request_id = runtime_control_request_id(worker_id, uuid.uuid4().hex)
        redis = await get_redis_client()
        reply_stream_key = control_reply_stream(request_id)
        control_stream_key = control_stream(worker_id)
        # P1-DR-04: deadline 以 Redis 服务端时钟为准，消除跨机器时钟偏移。
        expires_at_ms = await redis_server_now_ms(redis) + int(timeout_seconds * 1000)

        data = build_runtime_manage_control_payload(
            action=action,
            request_id=request_id,
            reply_stream=reply_stream_key,
            payload=payload or {},
            expires_at_ms=expires_at_ms,
        )

        await write_control_event(redis, control_stream_key, data)

        timeout_ms = int(timeout_seconds * 1000)
        result = await redis.xread({reply_stream_key: "0-0"}, count=1, block=timeout_ms)

        if not result:
            logger.warning(f"运行时控制超时: action={action}, worker={worker_id}")
            return {
                "success": False,
                "error": "运行时控制超时",
                "data": None,
            }

        _, messages = result[0]
        if not messages:
            return {"success": False, "error": "控制响应为空", "data": None}

        msg_id, raw = messages[0]
        _ = msg_id
        decoded = decode_stream_payload(raw)
        if str(decoded.get("request_id") or "") != request_id:
            raise RuntimeError("运行时控制响应 request_id 不匹配")

        return _decode_runtime_response(decoded)

    async def list_envs(self, worker_id: str, scope: str | None = None) -> dict[str, Any]:
        return await self.send_command(worker_id, "list_envs", {"scope": scope or ""})

    async def get_env(self, worker_id: str, env_name: str) -> dict[str, Any]:
        return await self.send_command(worker_id, "get_env", {"env_name": env_name})

    async def update_env(
        self,
        worker_id: str,
        env_name: str,
        key: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        normalized_key, normalized_description = validate_runtime_metadata(key, description)
        return await self.send_command(
            worker_id,
            "update_env",
            {
                "env_name": env_name,
                "key": normalized_key,
                "description": normalized_description,
            },
        )

    async def create_env(
        self,
        worker_id: str,
        env_name: str,
        python_version: str | None = None,
        *,
        packages: list[str] | None = None,
        created_by: str | None = None,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_creator, normalized_owner = validate_runtime_creator(created_by, owner_user_id)
        return await self.send_command(
            worker_id,
            "create_env",
            {
                "env_name": env_name,
                "python_version": python_version,
                "packages": packages or [],
                "created_by": normalized_creator or "",
                "owner_user_id": normalized_owner or "",
            },
            timeout=600,
        )

    async def delete_env(self, worker_id: str, env_name: str) -> dict[str, Any]:
        return await self.send_command(worker_id, "delete_env", {"env_name": env_name})

    async def list_packages(self, worker_id: str, env_name: str) -> dict[str, Any]:
        return await self.send_command(worker_id, "list_packages", {"env_name": env_name}, timeout=120)

    async def install_packages(
        self,
        worker_id: str,
        env_name: str,
        packages: list[str],
        upgrade: bool = False,
    ) -> dict[str, Any]:
        return await self.send_command(
            worker_id,
            "install_packages",
            {"env_name": env_name, "packages": packages, "upgrade": upgrade},
            timeout=900,
        )

    async def uninstall_packages(self, worker_id: str, env_name: str, packages: list[str]) -> dict[str, Any]:
        return await self.send_command(
            worker_id,
            "uninstall_packages",
            {"env_name": env_name, "packages": packages},
            timeout=300,
        )

    async def get_platform_info(self, worker_id: str) -> dict[str, Any]:
        return await self.send_command(worker_id, "get_platform_info", {})


runtime_control_service = RuntimeControlService()

__all__ = [
    "RuntimeControlService",
    "redis_server_now_ms",
    "runtime_control_service",
    "write_control_event",
]
