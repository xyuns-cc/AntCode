"""Prove the running Gateway container swaps TLS material from disk, no restart.

``tls_material`` 的卖点是"改盘即生效"。单测里用真实握手验过，但那是宿主进程里的
``grpc.aio.server``；生产画像下究竟成不成立，只有对着**真在跑的 Gateway 容器**换盘
才算数。这里就是那一步。

每一相都双向钉死，任一方向缺失都会被判失败：

1. 材料不变时反复握手 —— 重载日志一条都不该多（指纹短路生效）；
2. 同一 CA 重签服务端证书 —— 服务端出示的必须**正好**是刚写进去的那张（新材料
   生效），也就必然不再是原来那张（旧材料退场）；
3. 把客户端 CA bundle 换成另一家 —— 新 CA 客户端可连 **且** 旧 CA 客户端被拒
   （CA 级吊销真的生效，不是"新旧都收"）；
4. 全部写回原值 —— 旧客户端恢复可连、新客户端被拒、服务端证书指纹回到初始值；
5. 全程 PID / 启动时间 / 重启次数不变 —— 排除"其实是重启了"。

第 3 相会让**未持有新 CA 证书**的 Worker 在那几秒内建不了新连接（已建立的长连接
不受影响，材料只对新连接生效）。窗口刻意压到两次握手，紧跟着第 4 相还原。
"""

from __future__ import annotations

import argparse
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from scripts.release_e2e_endpoints import release_endpoints
from scripts.release_e2e_environment import RUNNER_HOST
from scripts.release_e2e_pki import _create_ca, _create_leaf, _pem_cert, _pem_key
from scripts.release_e2e_tls_probe import (
    ClientIdentity,
    ContainerIdentity,
    channel_becomes_ready,
    container_identity,
    reload_count,
    replace_material,
    served_certificate,
)

#: 材料不变时的重复握手次数。指纹短路一旦失效，这些握手会各留一条重载日志。
IDLE_HANDSHAKES = 5
#: 冒充身份的 CN；只要不与在册 Worker 撞名即可，认证强度不在本校验的射程内。
CHALLENGER_COMMON_NAME = "tls-hot-reload-challenger"
SERVER_CERTIFICATE = "server.crt"
SERVER_KEY = "server.key"
CLIENT_CA = "ca.crt"
MATERIAL_FILES = (SERVER_CERTIFICATE, SERVER_KEY, CLIENT_CA)


@dataclass(frozen=True)
class _Authority:
    key: rsa.RSAPrivateKey
    certificate: x509.Certificate


@dataclass(frozen=True)
class _Authorities:
    """现网 CA 用来重签服务端证书（Worker 无感），另起的 CA 用来验 CA 级吊销。"""

    incumbent: _Authority
    challenger: _Authority


@dataclass(frozen=True)
class _Probe:
    directory: Path
    endpoint: tuple[str, int]
    container: str
    #: 现网 CA 签发的客户端；第 3 相之后它必须被拒。
    incumbent: ClientIdentity
    #: 新 CA 签发的客户端；第 3 相之前它必须被拒。
    challenger: ClientIdentity


@dataclass(frozen=True)
class _Baseline:
    identity: ContainerIdentity
    material: dict[str, bytes]
    certificate: str
    reloads: int


def _arguments() -> argparse.Namespace:
    """Gateway 端口从本轮 `production.env` 读，脚本不再自带一份 15051 默认值。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", type=Path, required=True, help="本轮 production.env")
    parser.add_argument("--gateway-tls-dir", type=Path, required=True, help="宿主侧目录，绑定到容器 /etc/antcode/tls")
    parser.add_argument("--ca-cert", type=Path, required=True)
    parser.add_argument("--ca-key", type=Path, required=True, help="签发现网服务端证书的 CA 私钥，用于重签")
    parser.add_argument("--client-cert", type=Path, required=True)
    parser.add_argument("--client-key", type=Path, required=True)
    parser.add_argument("--gateway-container", required=True, help="docker inspect / docker logs 的目标容器")
    parser.add_argument("--gateway-host", default=RUNNER_HOST)
    parsed = parser.parse_args()
    return argparse.Namespace(**vars(parsed), gateway_port=release_endpoints(parsed.environment).gateway_port)


def _require(satisfied: bool, failure: str) -> None:
    if not satisfied:
        raise RuntimeError(f"Gateway TLS hot reload: {failure}")


def _load_authority(key_path: Path, certificate_path: Path) -> _Authority:
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Gateway CA private key must be RSA")
    return _Authority(key=key, certificate=x509.load_pem_x509_certificate(certificate_path.read_bytes()))


def _fingerprint(certificate: x509.Certificate) -> str:
    return hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()


def _issue_challenger(authority: _Authority, scratch: Path) -> ClientIdentity:
    """另起一套 CA 并签一张客户端证书，用来验证 CA 级吊销的两个方向。"""
    key, certificate = _create_leaf(authority.key, authority.certificate, CHALLENGER_COMMON_NAME, client=True)
    (scratch / "challenger.key").write_text(_pem_key(key), encoding="utf-8")
    (scratch / "challenger.crt").write_text(_pem_cert(certificate), encoding="utf-8")
    return ClientIdentity(
        certificate=scratch / "challenger.crt",
        private_key=scratch / "challenger.key",
        # 服务端证书自始至终由现网 CA 签发，所以校验服务端仍然用现网 CA。
        trusted_ca=scratch / "incumbent-ca.crt",
    )


def _build_probe(arguments: argparse.Namespace, scratch: Path) -> tuple[_Probe, _Authorities]:
    (scratch / "incumbent-ca.crt").write_bytes(arguments.ca_cert.read_bytes())
    challenger_key, challenger_certificate = _create_ca()
    authorities = _Authorities(
        incumbent=_load_authority(arguments.ca_key, arguments.ca_cert),
        challenger=_Authority(key=challenger_key, certificate=challenger_certificate),
    )
    probe = _Probe(
        directory=arguments.gateway_tls_dir,
        endpoint=(arguments.gateway_host, arguments.gateway_port),
        container=arguments.gateway_container,
        incumbent=ClientIdentity(
            certificate=arguments.client_cert,
            private_key=arguments.client_key,
            trusted_ca=arguments.ca_cert,
        ),
        challenger=_issue_challenger(authorities.challenger, scratch),
    )
    return probe, authorities


def _capture_baseline(probe: _Probe) -> _Baseline:
    return _Baseline(
        identity=container_identity(probe.container),
        material={name: (probe.directory / name).read_bytes() for name in MATERIAL_FILES},
        certificate=served_certificate(probe.endpoint, probe.incumbent),
        reloads=reload_count(probe.container),
    )


def _require_idle_handshakes_do_not_reload(probe: _Probe, expected: int) -> None:
    """材料没变时反复握手，重载日志必须一条都不多——否则每次握手都在白读盘。"""
    for _ in range(IDLE_HANDSHAKES):
        served_certificate(probe.endpoint, probe.incumbent)
    observed = reload_count(probe.container)
    _require(observed == expected, f"unchanged material was re-read per handshake ({expected} -> {observed})")


def _rotate_server_certificate(probe: _Probe, authority: _Authority) -> str:
    """用现网 CA 重签一张服务端证书；Worker 侧信任链不变，换证对它们零影响。"""
    current = x509.load_pem_x509_certificate((probe.directory / SERVER_CERTIFICATE).read_bytes())
    common_name = str(current.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value)
    key, certificate = _create_leaf(authority.key, authority.certificate, common_name, client=False)
    replace_material(probe.directory / SERVER_KEY, _pem_key(key).encode())
    replace_material(probe.directory / SERVER_CERTIFICATE, _pem_cert(certificate).encode())
    return _fingerprint(certificate)


def _phase_rotate_server_certificate(probe: _Probe, authority: _Authority, baseline: _Baseline) -> None:
    expected = _rotate_server_certificate(probe, authority)
    _require(expected != baseline.certificate, "re-signed server certificate is identical to the incumbent one")
    served = served_certificate(probe.endpoint, probe.incumbent)
    _require(served == expected, "Gateway kept serving the previous certificate after the swap")
    reloads = reload_count(probe.container)
    _require(reloads > baseline.reloads, "swapping the server certificate left no reload trace in the container log")
    _require_idle_handshakes_do_not_reload(probe, reloads)


def _phase_revoke_incumbent_ca(probe: _Probe, authority: _Authority) -> None:
    replace_material(probe.directory / CLIENT_CA, _pem_cert(authority.certificate).encode())
    _require(channel_becomes_ready(probe.endpoint, probe.challenger), "rotated client CA was not honoured")
    _require(
        not channel_becomes_ready(probe.endpoint, probe.incumbent),
        "certificate from the removed CA is still accepted — the bundle was not actually replaced",
    )


def _restore(probe: _Probe, baseline: _Baseline) -> None:
    for name, value in baseline.material.items():
        replace_material(probe.directory / name, value)


def _phase_verify_restore(probe: _Probe, baseline: _Baseline) -> None:
    _require(channel_becomes_ready(probe.endpoint, probe.incumbent), "restoring the original CA bundle had no effect")
    _require(not channel_becomes_ready(probe.endpoint, probe.challenger), "the rotated CA is still trusted")
    served = served_certificate(probe.endpoint, probe.incumbent)
    _require(served == baseline.certificate, "the original server certificate was not restored")


def _verify(probe: _Probe, authorities: _Authorities) -> None:
    baseline = _capture_baseline(probe)
    _require_idle_handshakes_do_not_reload(probe, baseline.reloads)
    try:
        _phase_rotate_server_certificate(probe, authorities.incumbent, baseline)
        _phase_revoke_incumbent_ca(probe, authorities.challenger)
    finally:
        # 还原永远要做：留着轮换后的 CA bundle 会让后续 E2E 场景里的 Worker 全部连不上。
        _restore(probe, baseline)
    _phase_verify_restore(probe, baseline)
    identity = container_identity(probe.container)
    _require(identity == baseline.identity, f"Gateway restarted during the rotation: {baseline.identity} -> {identity}")


def main() -> None:
    arguments = _arguments()
    with tempfile.TemporaryDirectory() as scratch:
        probe, authorities = _build_probe(arguments, Path(scratch))
        _verify(probe, authorities)


if __name__ == "__main__":
    main()
