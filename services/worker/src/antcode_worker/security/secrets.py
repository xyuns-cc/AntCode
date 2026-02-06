"""
凭证管理

从 runtime_data/secrets/ 或环境变量加载凭证。
凭证不应该存入代码仓库。

支持 SIGHUP 信号触发重载。

Requirements: 11.2
"""

import os
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger


@dataclass
class Credential:
    """凭证数据"""
    key: str
    value: str
    source: str  # "file", "env", "default"
    path: str | None = None  # 文件来源路径


class SecretsManager:
    """
    凭证管理器

    从 runtime_data/secrets/ 或环境变量加载凭证。
    凭证不应该存入代码仓库。

    优先级：文件 > 环境变量 > 默认值

    Requirements: 11.2
    """

    # 已知的凭证键名
    KNOWN_KEYS = {
        "api_key": "ANTCODE_API_KEY",
        "ca_cert": "ANTCODE_CA_CERT_PATH",
        "client_cert": "ANTCODE_CLIENT_CERT_PATH",
        "client_key": "ANTCODE_CLIENT_KEY_PATH",
        "redis_password": "ANTCODE_REDIS_PASSWORD",
        "gateway_token": "ANTCODE_GATEWAY_TOKEN",
    }

    def __init__(
        self,
        secrets_dir: Path | None = None,
        env_prefix: str = "ANTCODE_",
        on_reload: Callable[[], None] | None = None,
    ):
        """
        初始化凭证管理器

        Args:
            secrets_dir: 凭证目录路径
            env_prefix: 环境变量前缀
            on_reload: 重载回调
        """
        self._secrets_dir = secrets_dir
        self._env_prefix = env_prefix
        self._on_reload = on_reload
        self._cache: dict[str, Credential] = {}
        self._lock = threading.RLock()
        self._signal_handler_installed = False

        # 初始化时加载所有已知凭证
        self._preload_known_secrets()

    def _preload_known_secrets(self) -> None:
        """预加载已知凭证"""
        for key in self.KNOWN_KEYS:
            self.get(key)

    def _env_key(self, key: str) -> str:
        """获取环境变量键名"""
        # 如果是已知键，使用预定义的环境变量名
        if key in self.KNOWN_KEYS:
            return self.KNOWN_KEYS[key]
        # 否则使用前缀 + 大写键名
        return f"{self._env_prefix}{key.upper()}"

    def _load_from_file(self, key: str) -> Credential | None:
        """从文件加载凭证"""
        if not self._secrets_dir:
            return None

        file_path = self._secrets_dir / key
        if not file_path.exists():
            return None

        try:
            value = file_path.read_text(encoding="utf-8").strip()
            if value:
                return Credential(
                    key=key,
                    value=value,
                    source="file",
                    path=str(file_path),
                )
        except Exception as e:
            logger.warning(f"读取凭证文件失败 {key}: {e}")

        return None

    def _load_from_env(self, key: str) -> Credential | None:
        """从环境变量加载凭证"""
        env_key = self._env_key(key)
        value = os.environ.get(env_key)

        if value:
            return Credential(
                key=key,
                value=value,
                source="env",
            )

        return None

    def get(self, key: str, default: str | None = None) -> str | None:
        """
        获取凭证

        优先级：缓存 > 文件 > 环境变量 > 默认值

        Args:
            key: 凭证键名
            default: 默认值

        Returns:
            凭证值或默认值
        """
        with self._lock:
            # 检查缓存
            if key in self._cache:
                return self._cache[key].value

            # 从文件加载
            credential = self._load_from_file(key)
            if credential:
                self._cache[key] = credential
                logger.debug(f"从文件加载凭证: {key}")
                return credential.value

            # 从环境变量加载
            credential = self._load_from_env(key)
            if credential:
                self._cache[key] = credential
                logger.debug(f"从环境变量加载凭证: {key}")
                return credential.value

            # 返回默认值
            if default is not None:
                self._cache[key] = Credential(
                    key=key,
                    value=default,
                    source="default",
                )
                return default

            return None

    def get_credential(self, key: str) -> Credential | None:
        """获取凭证对象（包含来源信息）"""
        with self._lock:
            # 确保凭证已加载
            self.get(key)
            return self._cache.get(key)

    def get_api_key(self) -> str | None:
        """获取 API Key"""
        return self.get("api_key")

    def get_gateway_token(self) -> str | None:
        """获取 Gateway Token"""
        return self.get("gateway_token")

    def get_redis_password(self) -> str | None:
        """获取 Redis 密码"""
        return self.get("redis_password")

    def get_ca_cert_path(self) -> str | None:
        """
        获取 CA 证书路径

        优先检查 secrets 目录下的 ca.crt 文件，
        然后检查环境变量指定的路径。
        """
        # 检查 secrets 目录
        if self._secrets_dir:
            ca_path = self._secrets_dir / "ca.crt"
            if ca_path.exists():
                return str(ca_path)

        # 检查环境变量
        return self.get("ca_cert")

    def get_client_cert_paths(self) -> tuple[str, str] | None:
        """
        获取客户端证书和密钥路径

        Returns:
            (cert_path, key_path) 或 None
        """
        cert_path = None
        key_path = None

        # 检查 secrets 目录
        if self._secrets_dir:
            cert_file = self._secrets_dir / "client.crt"
            key_file = self._secrets_dir / "client.key"
            if cert_file.exists():
                cert_path = str(cert_file)
            if key_file.exists():
                key_path = str(key_file)

        # 如果文件不存在，检查环境变量
        if not cert_path:
            cert_path = self.get("client_cert")
        if not key_path:
            key_path = self.get("client_key")

        if cert_path and key_path:
            return (cert_path, key_path)

        return None

    def has_mtls_certs(self) -> bool:
        """检查是否有 mTLS 证书"""
        ca_cert = self.get_ca_cert_path()
        client_certs = self.get_client_cert_paths()
        return ca_cert is not None and client_certs is not None

    def has_api_key(self) -> bool:
        """检查是否有 API Key"""
        return self.get_api_key() is not None

    def has_gateway_token(self) -> bool:
        """检查是否有 Gateway Token"""
        return self.get_gateway_token() is not None

    def reload(self) -> None:
        """
        重新加载凭证

        清空缓存并重新加载所有已知凭证。
        """
        with self._lock:
            old_cache = self._cache.copy()
            self._cache.clear()
            self._preload_known_secrets()

            # 检查变更
            changed_keys = []
            for key in self.KNOWN_KEYS:
                old_cred = old_cache.get(key)
                new_cred = self._cache.get(key)
                old_value = old_cred.value if old_cred else None
                new_value = new_cred.value if new_cred else None
                if old_value != new_value:
                    changed_keys.append(key)

            if changed_keys:
                logger.info(f"凭证已重载，变更: {changed_keys}")
            else:
                logger.debug("凭证已重载，无变更")

            if self._on_reload:
                try:
                    self._on_reload()
                except Exception as e:
                    logger.error(f"凭证重载回调失败: {e}")

    def clear_cache(self) -> None:
        """清空缓存"""
        with self._lock:
            self._cache.clear()
            logger.debug("凭证缓存已清空")

    def list_loaded(self) -> list[str]:
        """列出已加载的凭证键名"""
        with self._lock:
            return list(self._cache.keys())

    def get_sources(self) -> dict[str, str]:
        """获取所有凭证的来源"""
        with self._lock:
            return {key: cred.source for key, cred in self._cache.items()}

    def install_signal_handler(self) -> None:
        """
        安装 SIGHUP 信号处理器

        收到 SIGHUP 信号时重载凭证。
        仅在 Unix 系统上有效。
        """
        if self._signal_handler_installed:
            return

        try:
            def handler(signum, frame):
                logger.info("收到 SIGHUP 信号，重载凭证...")
                self.reload()

            signal.signal(signal.SIGHUP, handler)
            self._signal_handler_installed = True
            logger.debug("已安装凭证 SIGHUP 信号处理器")
        except (AttributeError, ValueError) as e:
            # Windows 不支持 SIGHUP
            logger.debug(f"无法安装 SIGHUP 处理器: {e}")

    def uninstall_signal_handler(self) -> None:
        """卸载 SIGHUP 信号处理器"""
        if not self._signal_handler_installed:
            return

        try:
            signal.signal(signal.SIGHUP, signal.SIG_DFL)
            self._signal_handler_installed = False
            logger.debug("已卸载凭证 SIGHUP 信号处理器")
        except (AttributeError, ValueError):
            pass

    def validate(self) -> dict[str, bool]:
        """
        验证凭证配置

        Returns:
            dict: 各凭证的验证结果
        """
        results = {}

        # 检查 API Key
        results["api_key"] = self.has_api_key()

        # 检查 Gateway Token
        results["gateway_token"] = self.has_gateway_token()

        # 检查 mTLS 证书
        results["mtls_certs"] = self.has_mtls_certs()

        # 检查 CA 证书
        ca_path = self.get_ca_cert_path()
        results["ca_cert"] = ca_path is not None and Path(ca_path).exists() if ca_path else False

        # 检查客户端证书
        client_certs = self.get_client_cert_paths()
        if client_certs:
            cert_path, key_path = client_certs
            results["client_cert"] = Path(cert_path).exists()
            results["client_key"] = Path(key_path).exists()
        else:
            results["client_cert"] = False
            results["client_key"] = False

        return results


# 全局凭证管理器实例
_secrets_manager: SecretsManager | None = None


def get_secrets_manager() -> SecretsManager | None:
    """获取全局凭证管理器"""
    return _secrets_manager


def set_secrets_manager(manager: SecretsManager) -> None:
    """设置全局凭证管理器"""
    global _secrets_manager
    _secrets_manager = manager


def init_secrets_manager(
    secrets_dir: Path | None = None,
    env_prefix: str = "ANTCODE_",
    on_reload: Callable[[], None] | None = None,
    install_signal_handler: bool = True,
) -> SecretsManager:
    """
    初始化全局凭证管理器

    Args:
        secrets_dir: 凭证目录路径
        env_prefix: 环境变量前缀
        on_reload: 重载回调
        install_signal_handler: 是否安装 SIGHUP 处理器

    Returns:
        SecretsManager: 凭证管理器实例
    """
    manager = SecretsManager(
        secrets_dir=secrets_dir,
        env_prefix=env_prefix,
        on_reload=on_reload,
    )

    if install_signal_handler:
        manager.install_signal_handler()

    set_secrets_manager(manager)

    # 记录加载状态
    loaded = manager.list_loaded()
    sources = manager.get_sources()
    logger.info(f"凭证管理器已初始化，已加载: {len(loaded)} 个凭证")
    for key in loaded:
        logger.debug(f"  - {key}: {sources.get(key, 'unknown')}")

    return manager
