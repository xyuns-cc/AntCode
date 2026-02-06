"""
Worker 身份管理

管理 worker_id, labels, zone，跨重启持久化稳定身份。

Requirements: 11.1
"""

import signal
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from antcode_worker.utils.ids import generate_worker_id


@dataclass
class Identity:
    """
    Worker 身份

    管理 worker_id, labels, zone，跨重启持久化。
    支持 SIGHUP 信号触发重载。

    Requirements: 11.1
    """

    worker_id: str
    labels: dict[str, str] = field(default_factory=dict)
    zone: str = "default"

    # 元数据
    hostname: str = ""
    ip: str = ""

    # 版本信息
    version: str = "1.0.0"

    # 时间戳
    created_at: datetime | None = None
    last_loaded_at: datetime | None = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        self.last_loaded_at = datetime.now()

    @classmethod
    def load(cls, path: Path) -> Optional["Identity"]:
        """从文件加载身份"""
        if not path.exists():
            logger.debug(f"身份文件不存在: {path}")
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            if not data:
                logger.warning(f"身份文件为空: {path}")
                return None

            # 处理时间戳字段
            if "created_at" in data and isinstance(data["created_at"], str):
                try:
                    data["created_at"] = datetime.fromisoformat(data["created_at"])
                except ValueError:
                    data["created_at"] = None

            # 移除不需要的字段
            data.pop("last_loaded_at", None)

            identity = cls(**data)
            logger.info(f"已加载身份: worker_id={identity.worker_id}, zone={identity.zone}")
            return identity
        except Exception as e:
            logger.warning(f"加载身份失败: {e}")
            return None

    def save(self, path: Path) -> bool:
        """
        保存身份到文件

        Returns:
            bool: 保存是否成功
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "worker_id": self.worker_id,
                "labels": self.labels,
                "zone": self.zone,
                "hostname": self.hostname,
                "ip": self.ip,
                "version": self.version,
                "created_at": self.created_at.isoformat() if self.created_at else None,
            }
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            logger.debug(f"身份已保存: {path}")
            return True
        except Exception as e:
            logger.error(f"保存身份失败: {e}")
            return False

    @classmethod
    def generate(
        cls,
        zone: str = "default",
        labels: dict | None = None,
        version: str = "1.0.0",
    ) -> "Identity":
        """生成新身份"""
        hostname = _get_hostname()
        ip = _get_local_ip()

        return cls(
            worker_id=generate_worker_id(),
            labels=labels or {},
            zone=zone,
            hostname=hostname,
            ip=ip,
            version=version,
            created_at=datetime.now(),
        )

    @classmethod
    def load_or_generate(
        cls,
        path: Path,
        zone: str = "default",
        labels: dict | None = None,
        version: str = "1.0.0",
    ) -> "Identity":
        """
        加载或生成身份

        如果文件存在且有效，加载现有身份；
        否则生成新身份并保存。
        """
        identity = cls.load(path)
        if identity:
            # 更新可变字段（保持 worker_id 不变）
            if labels:
                identity.labels.update(labels)
            if zone != "default":
                identity.zone = zone
            identity.version = version
            identity.hostname = _get_hostname()
            identity.ip = _get_local_ip()
            identity.save(path)
            return identity

        identity = cls.generate(zone=zone, labels=labels, version=version)
        identity.save(path)
        logger.info(f"已生成新身份: {identity.worker_id}")
        return identity

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "worker_id": self.worker_id,
            "labels": self.labels,
            "zone": self.zone,
            "hostname": self.hostname,
            "ip": self.ip,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_loaded_at": self.last_loaded_at.isoformat() if self.last_loaded_at else None,
        }

    def matches_labels(self, required_labels: dict[str, str]) -> bool:
        """检查是否匹配所需标签"""
        return all(self.labels.get(key) == value for key, value in required_labels.items())


class IdentityManager:
    """
    身份管理器

    提供身份的加载、保存、重载功能。
    支持 SIGHUP 信号触发重载。

    Requirements: 11.1
    """

    def __init__(
        self,
        identity_path: Path,
        zone: str = "default",
        labels: dict | None = None,
        version: str = "1.0.0",
        on_reload: Callable[["Identity"], None] | None = None,
    ):
        self._path = identity_path
        self._zone = zone
        self._labels = labels or {}
        self._version = version
        self._on_reload = on_reload
        self._identity: Identity | None = None
        self._lock = threading.RLock()
        self._signal_handler_installed = False

    @property
    def identity(self) -> Identity:
        """获取当前身份"""
        with self._lock:
            if self._identity is None:
                self._identity = Identity.load_or_generate(
                    self._path,
                    zone=self._zone,
                    labels=self._labels,
                    version=self._version,
                )
            return self._identity

    @property
    def worker_id(self) -> str:
        """获取 worker_id"""
        return self.identity.worker_id

    @property
    def zone(self) -> str:
        """获取 zone"""
        return self.identity.zone

    @property
    def labels(self) -> dict[str, str]:
        """获取 labels"""
        return self.identity.labels

    def reload(self) -> Identity:
        """
        重新加载身份

        从文件重新加载身份信息（保持 worker_id 不变）。
        """
        with self._lock:
            old_identity = self._identity
            new_identity = Identity.load(self._path)

            if new_identity:
                # 保持 worker_id 不变
                if old_identity and new_identity.worker_id != old_identity.worker_id:
                    logger.warning(
                        f"身份文件中的 worker_id 已更改，保持原有 ID: "
                        f"{old_identity.worker_id} (文件中: {new_identity.worker_id})"
                    )
                    new_identity.worker_id = old_identity.worker_id

                self._identity = new_identity
                logger.info(f"身份已重载: {self._identity.worker_id}")

                if self._on_reload:
                    try:
                        self._on_reload(self._identity)
                    except Exception as e:
                        logger.error(f"身份重载回调失败: {e}")
            else:
                logger.warning("重载身份失败，保持现有身份")

            return self._identity or self.identity

    def update_labels(self, labels: dict[str, str]) -> None:
        """更新标签"""
        with self._lock:
            self.identity.labels.update(labels)
            self.identity.save(self._path)
            logger.debug(f"标签已更新: {labels}")

    def update_zone(self, zone: str) -> None:
        """更新区域"""
        with self._lock:
            self.identity.zone = zone
            self.identity.save(self._path)
            logger.debug(f"区域已更新: {zone}")

    def install_signal_handler(self) -> None:
        """
        安装 SIGHUP 信号处理器

        收到 SIGHUP 信号时重载身份。
        仅在 Unix 系统上有效。
        """
        if self._signal_handler_installed:
            return

        try:
            def handler(signum, frame):
                logger.info("收到 SIGHUP 信号，重载身份...")
                self.reload()

            signal.signal(signal.SIGHUP, handler)
            self._signal_handler_installed = True
            logger.debug("已安装 SIGHUP 信号处理器")
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
            logger.debug("已卸载 SIGHUP 信号处理器")
        except (AttributeError, ValueError):
            pass


def _get_hostname() -> str:
    """获取主机名"""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _get_local_ip() -> str:
    """获取本地 IP"""
    try:
        # 尝试连接外部地址获取本地 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


# 全局身份管理器实例
_identity_manager: IdentityManager | None = None


def get_identity_manager() -> IdentityManager | None:
    """获取全局身份管理器"""
    return _identity_manager


def set_identity_manager(manager: IdentityManager) -> None:
    """设置全局身份管理器"""
    global _identity_manager
    _identity_manager = manager


def init_identity_manager(
    identity_path: Path,
    zone: str = "default",
    labels: dict | None = None,
    version: str = "1.0.0",
    on_reload: Callable[[Identity], None] | None = None,
    install_signal_handler: bool = True,
) -> IdentityManager:
    """
    初始化全局身份管理器

    Args:
        identity_path: 身份文件路径
        zone: 区域
        labels: 标签
        version: 版本
        on_reload: 重载回调
        install_signal_handler: 是否安装 SIGHUP 处理器

    Returns:
        IdentityManager: 身份管理器实例
    """
    manager = IdentityManager(
        identity_path=identity_path,
        zone=zone,
        labels=labels,
        version=version,
        on_reload=on_reload,
    )

    # 触发身份加载
    _ = manager.identity

    if install_signal_handler:
        manager.install_signal_handler()

    set_identity_manager(manager)
    return manager
