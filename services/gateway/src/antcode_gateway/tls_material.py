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

**换料之前校验到哪一步**：``_validate`` 真解析，不看 PEM 标记在不在。原因是标记
判据对真实的半写入完全无效——中断的写留下的是"BEGIN 头完整、body 截断、没有
END"，标记查得到。实测五种坏材料都能骗过标记判据：截断证书、只剩 END 行的私钥、
加密私钥、新私钥配旧证书、尾部追加到一半的 CA bundle。它们的共同后果不是"gRPC
装上坏材料"——gRPC 会拒绝建 handshaker factory 并**沿用旧材料**——而是这里照样提交了
指纹并打出 ``TLS_MATERIAL_RELOADED``：下一次回调判"没变"直接返回 ``None``，从此一条
日志都没有，Gateway 带着旧材料静默跑到旧证书到期为止，而
``release_e2e_tls_probe.reload_count`` 把这条日志计成一次成功轮换。

校验因此覆盖 gRPC 装载时才会暴露的全部形态：证书链与 CA bundle 逐条解析得出来、
私钥解析得出来且不带口令、证书与私钥是同一对。**它挡不住的**只有一种：截断恰好落在
两个 PEM 块的边界上——那样得到的是一个语法完整、只是少了几张证书的 bundle，与"运维
本来就想删掉那几张"在字节层面无从区分。

代价不落在握手上：``_validate`` 只在指纹已经判出"变了"之后才跑，稳态握手一次都不
碰它。单次校验的大头是 RSA-2048 私钥解析（本机实测约 43ms，OpenSSL 导入时会做密钥
自洽性检查），只在真的换料时付一次。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import grpc
from antcode_core.common.exceptions import ConfigurationError
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from loguru import logger

# 结构化失败码。日志与测试一律匹配这些常量，禁止匹配中文描述——描述会漂移。
TLS_MATERIAL_READ_FAILED = "TLS_MATERIAL_READ_FAILED"
TLS_MATERIAL_INVALID_PEM = "TLS_MATERIAL_INVALID_PEM"
#: 成功换料同样要有码。容器级校验靠数这条日志的条数判断"指纹短路有没有生效"
#: （材料没变时它一条都不该多），匹配中文描述会在下一次措辞调整时静默失效。
TLS_MATERIAL_RELOADED = "TLS_MATERIAL_RELOADED"

#: 解析出的证书条数必须等于 BEGIN 头的条数。``load_pem_x509_certificates`` 会**静默
#: 丢掉**尾部那个只写了一半的块（实测"完整 CA + 截断证书"照样返回 1 条），所以"解析
#: 出至少一条"判不出 bundle 里少了一张 CA，要拿头的条数把它对上。
PEM_CERTIFICATE_MARKER = b"-----BEGIN CERTIFICATE-----"

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


def _load_certificates(pem: bytes, subject: str) -> list[x509.Certificate]:
    """真解析（与 ``tls_expiry`` 同一套 API），而不是查 PEM 标记在不在。

    运维中断的写留下的恰恰是"BEGIN 头完整、body 截断、没有 END"，标记查得到、
    内容用不了；按标记判必然放行。
    """
    try:
        certificates = x509.load_pem_x509_certificates(pem)
    except ValueError as exc:
        raise ConfigurationError(f"{TLS_MATERIAL_INVALID_PEM}: {subject}不是可解析的 PEM 证书: {exc}") from exc
    if len(certificates) != pem.count(PEM_CERTIFICATE_MARKER):
        raise ConfigurationError(f"{TLS_MATERIAL_INVALID_PEM}: {subject}里有只写了一半的证书块")
    return certificates


def _load_private_key(pem: bytes) -> PrivateKeyTypes:
    """加密私钥单列一条：gRPC 没有输入口令的入口，它解不开，装上去等于没装。

    ``load_pem_private_key(..., password=None)`` 对加密私钥抛的正是 ``TypeError``，
    所以这个分支不是猜的，它就是"这份材料带口令"的确切信号。
    """
    try:
        return serialization.load_pem_private_key(pem, password=None)
    except TypeError as exc:
        raise ConfigurationError(f"{TLS_MATERIAL_INVALID_PEM}: 服务端私钥是加密私钥，gRPC 无法使用") from exc
    except (UnsupportedAlgorithm, ValueError) as exc:
        raise ConfigurationError(f"{TLS_MATERIAL_INVALID_PEM}: 服务端私钥不是可解析的 PEM 私钥: {exc}") from exc


def _public_der(key: Any) -> bytes:
    """公钥的规范化字节，用来判 cert 与 key 是不是同一对；DER 编码唯一，可直接比。"""
    der: bytes = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return der


def _validate(material: TlsMaterial) -> None:
    """判据是"gRPC 拿得出可用的 handshaker"，不是"字节里有 PEM 字样"。"""
    certificates = _load_certificates(material.certificate, "服务端证书")
    private_key = _load_private_key(material.private_key)
    # 三份材料是顺序读的，轮换途中必然存在"新私钥已落盘、证书还是旧的"这个窗口：
    # 两边各自都解析得出来，只有配对检查看得出不对。gRPC 拿到不配对的一对会直接
    # 拒绝建 handshaker factory（实测 TSI_INVALID_ARGUMENT），继续沿用旧材料。
    if _public_der(certificates[0].public_key()) != _public_der(private_key.public_key()):
        raise ConfigurationError(f"{TLS_MATERIAL_INVALID_PEM}: 服务端证书与私钥不是同一对")
    if material.client_ca is not None:
        _load_certificates(material.client_ca, "客户端 CA bundle")


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
            # 不在这里拼失败码：``exc`` 的消息里带的就是抛出点选的那个码（读不出来是
            # READ_FAILED，解析不过是 INVALID_PEM）。固定拼一个前缀会把两类事故并成
            # 一类，按码计数的告警分不出该找运维的挂载还是找签发流程。
            logger.error("TLS 材料已变更但不可用，继续沿用旧材料: {}", exc)
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
