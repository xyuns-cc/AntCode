"""
环境变量凭证存储实现

从环境变量读取凭证，适用于容器化部署场景。

Requirements: 6.3, 6.4, 6.5, 6.6
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from antcode_worker.services.credential.base import CredentialStore
from antcode_worker.services.credential.registration_intent import (
    RegistrationIntent,
    RegistrationRequest,
)

# 环境变量名称前缀
ENV_PREFIX = "WORKER_CREDENTIAL_"

# 凭证字段与环境变量的映射


class EnvCredentialStore(CredentialStore):
    """
    环境变量凭证存储

    从环境变量读取凭证，适用于容器化部署场景。
    环境变量名称格式：WORKER_CREDENTIAL_<FIELD_NAME>

    支持的环境变量：
    - WORKER_CREDENTIAL_WORKER_ID: Worker ID
    - WORKER_CREDENTIAL_API_KEY: API 密钥
    - WORKER_CREDENTIAL_SECRET_KEY: 密钥
    - WORKER_CREDENTIAL_GATEWAY_HOST: Gateway 主机地址
    - WORKER_CREDENTIAL_GATEWAY_PORT: Gateway 端口
    - WORKER_CREDENTIAL_REGISTERED_AT: 注册时间

    Requirements: 6.3, 6.4, 6.5, 6.6
    """

    def __init__(self, env_prefix: str = ENV_PREFIX):
        """
        初始化环境变量凭证存储

        Args:
            env_prefix: 环境变量名称前缀
        """
        self._env_prefix = env_prefix
        self._env_mapping = {
            "worker_id": f"{env_prefix}WORKER_ID",
            "api_key": f"{env_prefix}API_KEY",
            "secret_key": f"{env_prefix}SECRET_KEY",
            "gateway_host": f"{env_prefix}GATEWAY_HOST",
            "gateway_port": f"{env_prefix}GATEWAY_PORT",
            "redis_username": f"{env_prefix}REDIS_USERNAME",
            "redis_password": f"{env_prefix}REDIS_PASSWORD",
            "registration_id": f"{env_prefix}REGISTRATION_ID",
            "registered_at": f"{env_prefix}REGISTERED_AT",
        }

    def ensure_durable_writable(self) -> None:
        raise RuntimeError("环境变量凭证存储不能耐久保存新签发的 Worker 凭证")

    @contextmanager
    def registration_session(
        self,
        install_key: str | None = None,
        request: RegistrationRequest | None = None,
    ) -> Iterator[RegistrationIntent | None]:
        del request
        if install_key is not None:
            raise RuntimeError("环境变量凭证存储不能创建可恢复注册意图")
        yield None

    def finish_registration(self) -> None:
        raise RuntimeError("环境变量凭证存储没有可完成的注册意图")

    def exists(self) -> bool:
        """
        检查凭证环境变量是否存在

        至少需要 worker_id、api_key、secret_key 三个必填字段
        """
        required_fields = ["worker_id", "api_key", "secret_key"]
        for field in required_fields:
            env_name = self._env_mapping.get(field)
            if not env_name or not os.getenv(env_name):
                return False
        return True

    def describe_location(self) -> str:
        return f"{ENV_PREFIX}* 环境变量"

    def load(self) -> dict[str, Any] | None:
        """从环境变量加载凭证；必填字段缺失返回 None。"""
        credentials: dict[str, Any] = {}
        for field, env_name in self._env_mapping.items():
            value = os.getenv(env_name)
            if value is None:
                continue
            credentials[field] = int(value) if field == "gateway_port" else value
        required_fields = ("worker_id", "api_key", "secret_key")
        if any(not credentials.get(field) for field in required_fields):
            return None
        return credentials

    async def load_async(self) -> dict[str, Any] | None:
        return self.load()

    def save(self, credentials: dict[str, Any]) -> bool:
        """恒抛：环境变量是只读来源，写进程内 env 不落盘，等于把新签发的凭证丢掉。

        返回类型上的 ``bool`` 只为满足 ``CredentialStore`` 接口，永远不会有返回值。
        """
        del credentials
        raise RuntimeError("环境变量凭证存储是只读来源")

    async def save_async(self, credentials: dict[str, Any]) -> bool:
        """同样恒抛，见 ``save``。"""
        return self.save(credentials)

    def clear(self) -> bool:
        """只从当前进程的 environ 里移除，不影响父进程或下次启动的注入值。"""
        for env_name in self._env_mapping.values():
            os.environ.pop(env_name, None)
        return True

    async def clear_async(self) -> bool:
        return self.clear()
