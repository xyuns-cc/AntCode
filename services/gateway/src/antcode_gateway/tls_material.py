"""Gateway TLS/mTLS 证书材料的热加载。

``grpc.ssl_server_credentials`` 在 ``add_secure_port`` 时把证书 / 私钥 / CA 一次性
读进 C core，之后再也不看磁盘。后果有二：

- 服务端证书到期只能靠重启 Gateway 换新，而重启会同时打断全部 Worker 的数据面
  长连接（``max_connection_age_ms`` 刻意不设，长连接本来就不会自然老化）；
- 客户端 CA bundle 同样钉死，摘掉一张被攻陷的 CA 也要重启才生效。

``dynamic_ssl_server_credentials`` 让 gRPC 在**每一次新 TLS 握手之前**回调 fetcher，
把"读盘"挪进 fetcher 就同时拿到轮换与 CA 级吊销：新连接立刻用新材料校验。

**边界**：这里只管新连接。已经建立的连接不会因为材料变化被踢掉——切断在途会话
靠控制面的 Worker 生命周期围栏（``lease_service.disable_worker`` 装 Redis fence，
``LeaseStore.is_current`` 在每条数据面消息上校验），与本模块相互独立。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import grpc
from antcode_core.common.exceptions import ConfigurationError
from loguru import logger

# 结构化失败码。日志与测试一律匹配这些常量，禁止匹配中文描述——描述会漂移。
TLS_MATERIAL_STAT_FAILED = "TLS_MATERIAL_STAT_FAILED"
TLS_MATERIAL_READ_FAILED = "TLS_MATERIAL_READ_FAILED"
TLS_MATERIAL_INVALID_PEM = "TLS_MATERIAL_INVALID_PEM"
#: 成功换料同样要有码。容器级校验靠数这条日志的条数判断"指纹短路有没有生效"
#: （材料没变时它一条都不该多），匹配中文描述会在下一次措辞调整时静默失效。
TLS_MATERIAL_RELOADED = "TLS_MATERIAL_RELOADED"

# PEM 边界标记。运维半写入 / 截断的文件如果直接换给 gRPC，全队 Worker 会在下一次
# 握手集体失败；换之前先validate，坏材料一律不生效。
PEM_CERTIFICATE_MARKER = b"-----BEGIN CERTIFICATE-----"
PEM_PRIVATE_KEY_MARKER = b"PRIVATE KEY-----"

#: ``(st_ino, st_mtime_ns, st_size)``——原子 replace 换 inode，就地改写换 mtime/size。
_Fingerprint = tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class TlsMaterialPaths:
    """磁盘上的 TLS 材料位置；``client_ca`` 为空表示单向 TLS。"""

    certificate: Path
    private_key: Path
    client_ca: Path | None = None

    @property
    def all_paths(self) -> tuple[Path, ...]:
        if self.client_ca is None:
            return (self.certificate, self.private_key)
        return (self.certificate, self.private_key, self.client_ca)


@dataclass(frozen=True, slots=True)
class TlsMaterial:
    """一次成功读盘得到的 PEM 字节。"""

    certificate: bytes
    private_key: bytes
    client_ca: bytes | None = None


def _validate(material: TlsMaterial) -> None:
    if PEM_CERTIFICATE_MARKER not in material.certificate:
        raise ConfigurationError(f"{TLS_MATERIAL_INVALID_PEM}: 服务端证书不是 PEM 证书")
    if PEM_PRIVATE_KEY_MARKER not in material.private_key:
        raise ConfigurationError(f"{TLS_MATERIAL_INVALID_PEM}: 服务端私钥不是 PEM 私钥")
    if material.client_ca is not None and PEM_CERTIFICATE_MARKER not in material.client_ca:
        raise ConfigurationError(f"{TLS_MATERIAL_INVALID_PEM}: 客户端 CA bundle 不含任何证书")


class TlsMaterialLoader:
    """按需重读 TLS 材料，并在内容未变时告诉 gRPC "不用换"。"""

    def __init__(self, paths: TlsMaterialPaths) -> None:
        self._paths = paths
        self._fingerprint: _Fingerprint | None = None

    def load(self) -> TlsMaterial:
        """读盘 + 校验。失败抛 ``ConfigurationError``，绝不返回半截材料。

        指纹在读之前取：若文件在读的过程中被改写，本次存下的是旧指纹，下一次
        握手会发现不一致并重读，改动不会被永久漏掉。
        """
        fingerprint = self._read_fingerprint()
        material = self._read_material()
        _validate(material)
        self._fingerprint = fingerprint
        return material

    def certificate_configuration(self) -> Any | None:
        """gRPC 每次新连接握手前的回调；返回 ``None`` 表示沿用当前材料。

        实测 grpc 1.76：fetcher 抛出的异常会被 gRPC **静默吞掉**并继续沿用上一次
        成功返回的材料。所以失败不能靠抛异常表达——必须自己落 ERROR 日志再显式
        ``return None``，让"这次没轮换成功"在日志里留下痕迹而不是无声无息。
        """
        fingerprint = self._read_fingerprint_or_none()
        if fingerprint is None:
            return None
        if fingerprint == self._fingerprint:
            return None
        try:
            material = self.load()
        except ConfigurationError as exc:
            logger.error("{}: TLS 材料已变更但不可用，继续沿用旧材料: {}", TLS_MATERIAL_READ_FAILED, exc)
            return None
        logger.warning("{}: TLS 材料已热更新，新连接开始使用新的证书 / CA bundle", TLS_MATERIAL_RELOADED)
        return server_certificate_configuration(material)

    def _read_fingerprint(self) -> _Fingerprint:
        entries = []
        for path in self._paths.all_paths:
            stat = path.stat()
            entries.append((stat.st_ino, stat.st_mtime_ns, stat.st_size))
        return tuple(entries)

    def _read_fingerprint_or_none(self) -> _Fingerprint | None:
        try:
            return self._read_fingerprint()
        except OSError as exc:
            logger.error("{}: TLS 材料无法 stat，继续沿用旧材料: {}", TLS_MATERIAL_STAT_FAILED, exc)
            return None

    def _read_material(self) -> TlsMaterial:
        try:
            certificate = self._paths.certificate.read_bytes()
            private_key = self._paths.private_key.read_bytes()
            client_ca = self._paths.client_ca.read_bytes() if self._paths.client_ca is not None else None
        except OSError as exc:
            raise ConfigurationError(f"{TLS_MATERIAL_READ_FAILED}: {exc}") from exc
        return TlsMaterial(certificate=certificate, private_key=private_key, client_ca=client_ca)


def server_certificate_configuration(material: TlsMaterial) -> Any:
    return grpc.ssl_server_certificate_configuration(
        [(material.private_key, material.certificate)],
        root_certificates=material.client_ca,
    )


def create_reloadable_server_credentials(paths: TlsMaterialPaths) -> grpc.ServerCredentials:
    """构造随磁盘热更新的服务端凭证。

    首次读盘在这里发生：材料缺失或损坏时直接抛，Gateway 起不来——启动期
    fail-closed，不允许"带着坏证书先跑起来"。
    """
    loader = TlsMaterialLoader(paths)
    initial = server_certificate_configuration(loader.load())
    return grpc.dynamic_ssl_server_credentials(
        initial,
        loader.certificate_configuration,
        require_client_authentication=paths.client_ca is not None,
    )


__all__ = [
    "TLS_MATERIAL_INVALID_PEM",
    "TLS_MATERIAL_READ_FAILED",
    "TLS_MATERIAL_RELOADED",
    "TLS_MATERIAL_STAT_FAILED",
    "TlsMaterial",
    "TlsMaterialLoader",
    "TlsMaterialPaths",
    "create_reloadable_server_credentials",
    "server_certificate_configuration",
]
