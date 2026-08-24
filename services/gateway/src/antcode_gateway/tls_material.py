"""Gateway TLS/mTLS 证书材料的热加载。

``grpc.ssl_server_credentials`` 在 ``add_secure_port`` 时把证书 / 私钥 / CA 一次性
读进 C core，之后再也不看磁盘。后果有二：

- 服务端证书到期只能靠重启 Gateway 换新，而重启会同时打断全部 Worker 的数据面
  长连接（``max_connection_age_ms`` 刻意不设，长连接本来就不会自然老化）；
- 客户端 CA bundle 同样钉死，摘掉一张被攻陷的 CA 也要重启才生效。

``dynamic_ssl_server_credentials`` 让 gRPC 在**每一次新 TLS 握手之前**回调 fetcher，
把"读盘"挪进 fetcher 就同时拿到轮换与 CA 级吊销：新连接立刻用新材料校验。

**边界**：这里只管新连接。已经建立的连接不会因为材料变化被踢掉。

这条边界曾被注为"切断在途会话靠 ``lease_service.disable_worker`` 的 Redis fence +
``LeaseStore.is_current`` 逐消息校验"。**在 CA 私钥泄露这个场景下该说法不成立**，
别再依赖它：``is_current`` 校验的是 ``(worker_id, lease_id)`` 的代际一致性，而持有
伪造证书者可以走正常 ``ControlService/Lease`` 给自己签发一个合法 lease——认证侧只
校验"证书 CN == 自己声明的 worker_id"，是一次自洽性检查，没有证书与注册表的绑定。
fence 又只由人工运维动作触发，与 CA 吊销没有任何自动联动。残留风险与缓解手段另行
记录，此处只声明：**本模块不提供在途会话的吊销能力，也不要假设下游有。**

**"变没变"为什么按内容哈希判**：这里原本用 ``(st_ino, st_mtime_ns, st_size)``
三元组，图的是省掉每次握手的读盘。但 stat 在 ext4 与 overlayfs 上都实测到两个盲区，
两个都表现为"内容换了、三元组一模一样"，于是 fetcher 返回 None、被吊销的 CA
继续被信任，而且**一条日志都不会有**：

- 写方把 mtime 还原（``cp -p`` / ``rsync -t`` / ``tar -x`` / ``touch -r``）：inode、
  size、mtime 全部不变，只有 ctime 变；
- 同一个时间戳 tick 内改写两次：内核给 inode 打的是粗粒度时间戳（本机 ext4 实测
  **4ms**），tick 内的第二次写连 ctime 都不变。

第二个盲区把 ``st_ctime_ns`` 也一并证伪了——加进三元组只挡得住第一个。stat 由此
不构成完备判据，而读盘 + sha256 的代价实测只占单次 mTLS 握手的约 1.3%
（约 31us vs 约 2.4ms），换的是"内容不同必然被发现"。

顺带消掉一类假阳性：单纯 ``touch`` 材料不再触发一次无谓的重载与 WARNING。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import grpc
from antcode_core.common.exceptions import ConfigurationError
from loguru import logger

# 结构化失败码。日志与测试一律匹配这些常量，禁止匹配中文描述——描述会漂移。
TLS_MATERIAL_READ_FAILED = "TLS_MATERIAL_READ_FAILED"
TLS_MATERIAL_INVALID_PEM = "TLS_MATERIAL_INVALID_PEM"
#: 成功换料同样要有码。容器级校验靠数这条日志的条数判断"指纹短路有没有生效"
#: （材料没变时它一条都不该多），匹配中文描述会在下一次措辞调整时静默失效。
TLS_MATERIAL_RELOADED = "TLS_MATERIAL_RELOADED"

# PEM 边界标记。运维半写入 / 截断的文件如果直接换给 gRPC，全队 Worker 会在下一次
# 握手集体失败；换之前先validate，坏材料一律不生效。
PEM_CERTIFICATE_MARKER = b"-----BEGIN CERTIFICATE-----"
PEM_PRIVATE_KEY_MARKER = b"PRIVATE KEY-----"

#: 逐份材料的 SHA-256 摘要。为什么不是 stat 三元组见模块 docstring。
_Fingerprint = tuple[bytes, ...]


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


def _fingerprint_of(material: TlsMaterial) -> _Fingerprint:
    """材料的内容指纹。

    元组长度只由 ``TlsMaterialPaths.client_ca`` 是否为空决定，而它是 frozen 的，
    所以同一个 loader 的指纹长度恒定，不会出现"长度变了所以不相等"这种伪变更。
    """
    blobs = (material.certificate, material.private_key, material.client_ca)
    return tuple(hashlib.sha256(blob).digest() for blob in blobs if blob is not None)


class TlsMaterialLoader:
    """按需重读 TLS 材料，并在内容未变时告诉 gRPC "不用换"。"""

    def __init__(self, paths: TlsMaterialPaths) -> None:
        self._paths = paths
        self._fingerprint: _Fingerprint | None = None

    def load(self) -> TlsMaterial:
        """读盘 + 校验。失败抛 ``ConfigurationError``，绝不返回半截材料。

        指纹由**实际装上去的那几段字节**算出，不存在"stat 与 read 之间被改写"的
        错位：读到什么就记什么，下一次回调读到不同内容必然不等。
        """
        material = self._read_material()
        _validate(material)
        self._fingerprint = _fingerprint_of(material)
        return material

    def certificate_configuration(self) -> Any | None:
        """gRPC 每次新连接握手前的回调；返回 ``None`` 表示沿用当前材料。

        实测 grpc 1.76：fetcher 抛出的异常会被 gRPC **静默吞掉**并继续沿用上一次
        成功返回的材料。所以失败不能靠抛异常表达——必须自己落 ERROR 日志再显式
        ``return None``，让"这次没轮换成功"在日志里留下痕迹而不是无声无息。
        """
        try:
            material = self._read_material()
            fingerprint = _fingerprint_of(material)
            if fingerprint == self._fingerprint:
                return None
            _validate(material)
        except ConfigurationError as exc:
            logger.error("{}: TLS 材料已变更但不可用，继续沿用旧材料: {}", TLS_MATERIAL_READ_FAILED, exc)
            return None
        # 校验通过才认指纹：坏材料不留痕，下一次回调还会重试而不是当成"已处理"。
        self._fingerprint = fingerprint
        logger.warning("{}: TLS 材料已热更新，新连接开始使用新的证书 / CA bundle", TLS_MATERIAL_RELOADED)
        return server_certificate_configuration(material)

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
    "TlsMaterial",
    "TlsMaterialLoader",
    "TlsMaterialPaths",
    "create_reloadable_server_credentials",
    "server_certificate_configuration",
]
