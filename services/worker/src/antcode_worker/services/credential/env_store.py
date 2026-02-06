"""
环境变量凭证存储实现

从环境变量读取凭证，适用于容器化部署场景。

Requirements: 6.3, 6.4, 6.5, 6.6
"""

import os
from typing import Any

from loguru import logger

from antcode_worker.services.credential.base import CredentialStore

# 环境变量名称前缀
ENV_PREFIX = "WORKER_CREDENTIAL_"

# 凭证字段与环境变量的映射
CREDENTIAL_ENV_MAPPING = {
    "worker_id": f"{ENV_PREFIX}WORKER_ID",
    "api_key": f"{ENV_PREFIX}API_KEY",
    "secret_key": f"{ENV_PREFIX}SECRET_KEY",
    "gateway_host": f"{ENV_PREFIX}GATEWAY_HOST",
    "gateway_port": f"{ENV_PREFIX}GATEWAY_PORT",
    "registered_at": f"{ENV_PREFIX}REGISTERED_AT",
}


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
            "registered_at": f"{env_prefix}REGISTERED_AT",
        }

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

    def load(self) -> dict[str, Any] | None:
        """
        从环境变量加载凭证（同步版本）

        Returns:
            凭证字典，如果必填字段缺失则返回 None

        Requirements: 6.4
        """
        try:
            credentials: dict[str, Any] = {}

            for field, env_name in self._env_mapping.items():
                value = os.getenv(env_name)
                if value is not None:
                    # 处理端口号类型转换
                    if field == "gateway_port":
                        try:
                            credentials[field] = int(value)
                        except ValueError:
                            credentials[field] = 0
                    else:
                        credentials[field] = value

            # 检查必填字段
            required_fields = ["worker_id", "api_key", "secret_key"]
            for field in required_fields:
                if not credentials.get(field):
                    logger.debug(f"环境变量凭证缺少必填字段: {field}")
                    return None

            logger.debug("已从环境变量加载凭证")
            return credentials

        except Exception as e:
            logger.error(f"从环境变量加载凭证失败: {e}")
            return None

    async def load_async(self) -> dict[str, Any] | None:
        """
        从环境变量加载凭证（异步版本）

        环境变量读取是同步操作，此方法直接调用同步版本。

        Returns:
            凭证字典，如果必填字段缺失则返回 None

        Requirements: 6.4
        """
        return self.load()

    def save(self, credentials: dict[str, Any]) -> bool:
        """
        保存凭证到环境变量（同步版本）

        注意：环境变量修改仅在当前进程有效，不会持久化。

        Args:
            credentials: 凭证字典

        Returns:
            是否保存成功

        Requirements: 6.5
        """
        try:
            for field, env_name in self._env_mapping.items():
                value = credentials.get(field)
                if value is not None:
                    os.environ[env_name] = str(value)

            logger.debug("已保存凭证到环境变量")
            return True

        except Exception as e:
            logger.error(f"保存凭证到环境变量失败: {e}")
            return False

    async def save_async(self, credentials: dict[str, Any]) -> bool:
        """
        保存凭证到环境变量（异步版本）

        环境变量设置是同步操作，此方法直接调用同步版本。

        Args:
            credentials: 凭证字典

        Returns:
            是否保存成功

        Requirements: 6.5
        """
        return self.save(credentials)

    def clear(self) -> bool:
        """
        清除凭证环境变量（同步版本）

        Returns:
            是否清除成功

        Requirements: 6.6
        """
        try:
            for env_name in self._env_mapping.values():
                if env_name in os.environ:
                    del os.environ[env_name]

            logger.debug("已清除凭证环境变量")
            return True

        except Exception as e:
            logger.error(f"清除凭证环境变量失败: {e}")
            return False

    async def clear_async(self) -> bool:
        """
        清除凭证环境变量（异步版本）

        环境变量删除是同步操作，此方法直接调用同步版本。

        Returns:
            是否清除成功

        Requirements: 6.6
        """
        return self.clear()
