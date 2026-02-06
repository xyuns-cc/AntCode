"""运行时管理控制服务"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

from loguru import logger

from antcode_core.infrastructure.redis import get_redis_client


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
        reply_stream = f"antcode:control:reply:{request_id}"
        control_stream = f"antcode:control:{worker_id}"

        data = {
            "control_type": "runtime_manage",
            "action": action,
            "request_id": request_id,
            "reply_stream": reply_stream,
            "payload": json.dumps(payload or {}, ensure_ascii=False),
        }

        await redis.xadd(control_stream, data)

        timeout_ms = int((timeout or self._default_timeout) * 1000)
        result = await redis.xread({reply_stream: "0-0"}, count=1, block=timeout_ms)

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
        decoded = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in raw.items()
        }

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
            await redis.delete(reply_stream)

        return {"success": success, "error": error, "data": data_obj}

    async def list_envs(self, worker_id: str, scope: str | None = None) -> dict[str, Any]:
        return await self.send_command(worker_id, "list_envs", {"scope": scope or ""})

    async def get_env(self, worker_id: str, env_name: str) -> dict[str, Any]:
        return await self.send_command(worker_id, "get_env", {"env_name": env_name})

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
        return await self.send_command(
            worker_id, "list_packages", {"env_name": env_name}, timeout=120
        )

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

    async def uninstall_packages(
        self, worker_id: str, env_name: str, packages: list[str]
    ) -> dict[str, Any]:
        return await self.send_command(
            worker_id,
            "uninstall_packages",
            {"env_name": env_name, "packages": packages},
            timeout=300,
        )

    async def list_interpreters(self, worker_id: str) -> dict[str, Any]:
        return await self.send_command(worker_id, "list_interpreters", {})

    async def install_interpreter(self, worker_id: str, version: str) -> dict[str, Any]:
        return await self.send_command(
            worker_id, "install_interpreter", {"version": version}, timeout=1200
        )

    async def unregister_interpreter(
        self, worker_id: str, version: str | None = None, python_bin: str | None = None
    ) -> dict[str, Any]:
        return await self.send_command(
            worker_id,
            "unregister_interpreter",
            {"version": version or "", "python_bin": python_bin or ""},
        )

    async def uninstall_interpreter(self, worker_id: str, version: str) -> dict[str, Any]:
        return await self.send_command(
            worker_id,
            "uninstall_interpreter",
            {"version": version},
            timeout=900,
        )

    async def register_interpreter(
        self,
        worker_id: str,
        python_bin: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        return await self.send_command(
            worker_id,
            "register_interpreter",
            {"python_bin": python_bin, "version": version or ""},
        )

    async def get_python_versions(self, worker_id: str) -> dict[str, Any]:
        return await self.send_command(worker_id, "get_python_versions", {})

    async def get_platform_info(self, worker_id: str) -> dict[str, Any]:
        return await self.send_command(worker_id, "get_platform_info", {})


runtime_control_service = RuntimeControlService()

__all__ = ["RuntimeControlService", "runtime_control_service"]
