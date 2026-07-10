"""运行时管理控制服务"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

from loguru import logger

from antcode_core.infrastructure.redis import (
    build_runtime_manage_control_payload,
    control_reply_stream,
    control_stream,
    decode_stream_payload,
    get_redis_client,
)

# P2-24: control:{worker_id} stream 的近似最大长度,与 gateway 侧
# ``CONTROL_STREAM_MAXLEN`` 保持一致。以前 4 处 XADD(tasks.cancel /
# runs.cancel / workers.resources / runtime_manage)都是裸 xadd,没有任何
# maxlen 或 MINID 裁剪,每个 worker 的控制历史随着时间单调增长,Redis 内存
# 和 AOF 无限膨胀,消费端只 XACK 不裁剪。
#
# 现在所有生产方走 ``write_control_event`` 或者内联 ``maxlen=CONTROL_STREAM_MAXLEN,
# approximate=True``,让 stream 的近似长度限定在 1000 条以内,再叠加消费端
# 的裁剪(可能后续基于 XPENDING/group ACK 游标做精确 MINID 裁剪)。
CONTROL_STREAM_MAXLEN = 1_000


async def write_control_event(
    redis: Any,
    control_stream_key: str,
    payload: dict[str, Any],
    *,
    maxlen: int = CONTROL_STREAM_MAXLEN,
) -> str:
    """向指定 worker 的 control stream 写一条控制事件,带近似 maxlen 裁剪。

    生产方(web_api / runtime 服务)统一走这个入口,避免 4 处 XADD 各自
    忘记加 maxlen 导致 Redis 无界增长(P2-24)。

    Args:
        redis: aioredis 客户端。
        control_stream_key: 通常是 ``control_stream(worker_id)`` 的返回值。
        payload: 事件负载(由 ``build_cancel_control_payload`` /
            ``build_config_update_control_payload`` /
            ``build_runtime_manage_control_payload`` 之一构造)。
        maxlen: 近似上限,默认 ``CONTROL_STREAM_MAXLEN``。使用 ``~`` 语义
            (approximate=True),不牺牲写性能。

    Returns:
        新条目的 stream id(与 ``redis.xadd`` 返回值一致)。
    """
    return await redis.xadd(
        control_stream_key,
        payload,
        maxlen=maxlen,
        approximate=True,
    )


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
        redis = await get_redis_client()
        request_id = uuid.uuid4().hex
        reply_stream_key = control_reply_stream(request_id)
        control_stream_key = control_stream(worker_id)

        data = build_runtime_manage_control_payload(
            action=action,
            request_id=request_id,
            reply_stream=reply_stream_key,
            payload=payload or {},
        )

        # P2-24: 走公共 helper,写入时带上 CONTROL_STREAM_MAXLEN 近似裁剪。
        await write_control_event(redis, control_stream_key, data)

        timeout_ms = int((timeout or self._default_timeout) * 1000)
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

        success = str(decoded.get("success", "")).lower() in ("1", "true", "yes")
        error = decoded.get("error", "")
        data_raw = decoded.get("data", "")
        data_obj = None
        if data_raw:
            try:
                data_obj = json.loads(data_raw)
            except Exception:
                data_obj = data_raw

        with contextlib.suppress(Exception):
            await redis.delete(reply_stream_key)

        return {"success": success, "error": error, "data": data_obj}

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
        return await self.send_command(
            worker_id,
            "update_env",
            {
                "env_name": env_name,
                "key": key,
                "description": description,
            },
        )

    async def create_env(
        self,
        worker_id: str,
        env_name: str,
        python_version: str | None = None,
        packages: list[str] | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        return await self.send_command(
            worker_id,
            "create_env",
            {
                "env_name": env_name,
                "python_version": python_version,
                "packages": packages or [],
                "created_by": created_by or "",
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
    "CONTROL_STREAM_MAXLEN",
    "RuntimeControlService",
    "runtime_control_service",
    "write_control_event",
]
