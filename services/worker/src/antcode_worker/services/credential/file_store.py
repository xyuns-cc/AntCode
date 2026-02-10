"""
文件凭证存储实现

将凭证存储在本地 JSON 文件中。

Requirements: 6.2, 6.4, 6.5, 6.6
"""

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from antcode_worker.services.credential.base import CredentialStore
from antcode_worker.config import DATA_ROOT

# 默认凭证文件路径（统一放在项目根目录 data/worker/secrets 下）
DEFAULT_CREDENTIAL_FILE = DATA_ROOT / "secrets" / "worker_credentials.json"


class FileCredentialStore(CredentialStore):
    """
    文件凭证存储

    将凭证以 JSON 格式存储在本地文件中。

    Requirements: 6.2, 6.4, 6.5, 6.6
    """

    def __init__(self, credential_file: Path | None = None):
        """
        初始化文件凭证存储

        Args:
            credential_file: 凭证文件路径，默认为 data/worker/secrets/worker_credentials.json
        """
        self._credential_file = credential_file or DEFAULT_CREDENTIAL_FILE

    @property
    def credential_file(self) -> Path:
        """凭证文件路径"""
        return self._credential_file

    def exists(self) -> bool:
        """检查凭证文件是否存在"""
        return self._credential_file.exists()

    def load(self) -> dict[str, Any] | None:
        """
        从文件加载凭证（同步版本）

        Returns:
            凭证字典，如果文件不存在或无效则返回 None

        Requirements: 6.4
        """
        try:
            if not self._credential_file.exists():
                logger.debug(f"凭证文件不存在: {self._credential_file}")
                return None

            with open(self._credential_file, encoding="utf-8") as f:
                data = json.load(f)

            logger.debug(f"已加载凭证文件: {self._credential_file}")
            return data

        except json.JSONDecodeError as e:
            logger.error(f"凭证文件 JSON 解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"加载凭证失败: {e}")
            return None

    async def load_async(self) -> dict[str, Any] | None:
        """
        从文件加载凭证（异步版本）

        Returns:
            凭证字典，如果文件不存在或无效则返回 None

        Requirements: 6.4
        """
        try:
            if not self._credential_file.exists():
                logger.debug(f"凭证文件不存在: {self._credential_file}")
                return None

            try:
                import aiofiles
                async with aiofiles.open(self._credential_file, encoding="utf-8") as f:
                    content = await f.read()
            except ImportError:
                # 如果没有 aiofiles，使用同步读取
                with open(self._credential_file, encoding="utf-8") as f:
                    content = f.read()

            data = json.loads(content)
            logger.debug(f"已加载凭证文件: {self._credential_file}")
            return data

        except json.JSONDecodeError as e:
            logger.error(f"凭证文件 JSON 解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"加载凭证失败: {e}")
            return None

    def save(self, credentials: dict[str, Any]) -> bool:
        """
        保存凭证到文件（同步版本）

        Args:
            credentials: 凭证字典

        Returns:
            是否保存成功

        Requirements: 6.5
        """
        try:
            self._credential_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self._credential_file, "w", encoding="utf-8") as f:
                json.dump(credentials, f, indent=2, ensure_ascii=False)

            logger.debug(f"已保存凭证文件: {self._credential_file}")
            return True

        except Exception as e:
            logger.error(f"保存凭证失败: {e}")
            return False

    async def save_async(self, credentials: dict[str, Any]) -> bool:
        """
        保存凭证到文件（异步版本）

        Args:
            credentials: 凭证字典

        Returns:
            是否保存成功

        Requirements: 6.5
        """
        try:
            self._credential_file.parent.mkdir(parents=True, exist_ok=True)

            content = json.dumps(credentials, indent=2, ensure_ascii=False)

            try:
                import aiofiles
                async with aiofiles.open(self._credential_file, "w", encoding="utf-8") as f:
                    await f.write(content)
            except ImportError:
                # 如果没有 aiofiles，使用同步写入
                with open(self._credential_file, "w", encoding="utf-8") as f:
                    f.write(content)

            logger.debug(f"已保存凭证文件: {self._credential_file}")
            return True

        except Exception as e:
            logger.error(f"保存凭证失败: {e}")
            return False

    def clear(self) -> bool:
        """
        清除凭证文件（同步版本）

        Returns:
            是否清除成功

        Requirements: 6.6
        """
        try:
            if self._credential_file.exists():
                self._credential_file.unlink()
                logger.debug(f"已删除凭证文件: {self._credential_file}")
            return True

        except Exception as e:
            logger.error(f"清除凭证失败: {e}")
            return False

    async def clear_async(self) -> bool:
        """
        清除凭证文件（异步版本）

        Returns:
            是否清除成功

        Requirements: 6.6
        """
        try:
            if self._credential_file.exists():
                os.remove(self._credential_file)
                logger.debug(f"已删除凭证文件: {self._credential_file}")
            return True

        except Exception as e:
            logger.error(f"清除凭证失败: {e}")
            return False
