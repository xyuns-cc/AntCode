"""Gateway 的 mTLS 材料必须能在不重启的前提下轮换 / 吊销。

以前 ``GrpcServer._create_server_credentials`` 一次性把证书读进 gRPC C core，
之后再也不看磁盘：证书到期或某张 CA 被攻陷，唯一手段是重启 Gateway，而重启会
同时打断全部 Worker 的数据面长连接。这里用真实 TLS 握手证明"改盘即生效"。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import grpc
import pytest
from antcode_gateway.config import GatewayConfig
from antcode_gateway.server import GrpcServer
from antcode_gateway.tls_material import (
    TLS_MATERIAL_READ_FAILED,
    TLS_MATERIAL_RELOADED,
    TlsMaterialLoader,
    TlsMaterialPaths,
)
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from loguru import logger

from scripts.release_e2e_pki import _create_ca, _create_leaf, _pem_cert, _pem_key

HANDSHAKE_TIMEOUT_SECONDS = 5
#: 材料不变时的重复回调次数；每多读一轮盘就是每次握手多三次 IO。
REPEATED_HANDSHAKES = 5


class _Authority:
    """一套独立 CA + 由它签发的服务端 / 客户端证书。"""

    def __init__(self, worker_id: str) -> None:
        ca_key, ca_cert = _create_ca()
        server_key, server_cert = _create_leaf(ca_key, ca_cert, "gateway", client=False)
        client_key, client_cert = _create_leaf(ca_key, ca_cert, worker_id, client=True)
        self.ca = _pem_cert(ca_cert).encode()
        self.server_key = _pem_key(server_key).encode()
        self.server_cert = _pem_cert(server_cert).encode()
        self.client_key = _pem_key(client_key).encode()
        self.client_cert = _pem_cert(client_cert).encode()


@dataclass(frozen=True, slots=True)
class _Authorities:
    """成对下发，免得每个用例都要多收一个 fixture 撞上位置参数上限。"""

    current: _Authority
    replacement: _Authority


@pytest.fixture(scope="module")
def authorities() -> _Authorities:
    return _Authorities(current=_Authority("worker-a"), replacement=_Authority("worker-b"))


def _write_material(root: Path, serving: _Authority, trusted_ca: bytes) -> TlsMaterialPaths:
    (root / "server.crt").write_bytes(serving.server_cert)
    (root / "server.key").write_bytes(serving.server_key)
    (root / "ca.crt").write_bytes(trusted_ca)
    return TlsMaterialPaths(
        certificate=root / "server.crt",
        private_key=root / "server.key",
        client_ca=root / "ca.crt",
    )


def _gateway_config(monkeypatch: pytest.MonkeyPatch, paths: TlsMaterialPaths) -> GatewayConfig:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("GRPC_TLS_CERT_PATH", str(paths.certificate))
    monkeypatch.setenv("GRPC_TLS_KEY_PATH", str(paths.private_key))
    monkeypatch.setenv("GRPC_TLS_CA_PATH", str(paths.client_ca))
    return GatewayConfig()


async def _handshake_succeeds(port: int, client: _Authority, server_ca: bytes) -> bool:
    """开一条全新连接跑一次 Health/Check —— 只有新连接才会触发 TLS 握手。"""
    credentials = grpc.ssl_channel_credentials(
        root_certificates=server_ca,
        private_key=client.client_key,
        certificate_chain=client.client_cert,
    )
    async with grpc.aio.secure_channel(
        f"localhost:{port}",
        credentials,
        options=(("grpc.ssl_target_name_override", "localhost"),),
    ) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        try:
            await stub.Check(health_pb2.HealthCheckRequest(), timeout=HANDSHAKE_TIMEOUT_SECONDS)
            return True
        except grpc.aio.AioRpcError:
            return False


async def _serve(credentials: grpc.ServerCredentials) -> tuple[grpc.aio.Server, int]:
    server = grpc.aio.server()
    health_pb2_grpc.add_HealthServicer_to_server(health.HealthServicer(), server)
    port = server.add_secure_port("localhost:0", credentials)
    await server.start()
    return server, port


@pytest.mark.asyncio
async def test_rotating_the_ca_bundle_on_disk_is_honoured_without_restarting_the_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: _Authorities,
) -> None:
    """把 CA-A 换成 CA-B 后：B 立刻可连，A 立刻被拒——全程不重启。

    钉死的是两个方向：新 CA 生效（轮换）与旧 CA 失效（吊销）。凭证一次性读盘时
    两条断言都会红。
    """
    authority_a, authority_b = authorities.current, authorities.replacement
    paths = _write_material(tmp_path, authority_a, authority_a.ca)
    config = _gateway_config(monkeypatch, paths)
    credentials = GrpcServer(config)._create_server_credentials()
    assert credentials is not None
    server, port = await _serve(credentials)
    try:
        assert await _handshake_succeeds(port, authority_a, authority_a.ca) is True
        assert await _handshake_succeeds(port, authority_b, authority_a.ca) is False

        paths.client_ca.write_bytes(authority_b.ca)

        assert await _handshake_succeeds(port, authority_b, authority_a.ca) is True
        assert await _handshake_succeeds(port, authority_a, authority_a.ca) is False
    finally:
        await server.stop(0)


@pytest.mark.asyncio
async def test_expired_style_server_certificate_swap_is_honoured_without_restarting_the_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: _Authorities,
) -> None:
    """服务端证书换成 B 家的之后，只信任 CA-B 的客户端才校验得过服务端身份。

    这条覆盖"服务端证书到期换新"这一路：静态凭证下服务端永远出示旧证书，
    ``_handshake_succeeds(..., authority_b.ca)`` 恒 False。
    """
    authority_a, authority_b = authorities.current, authorities.replacement
    paths = _write_material(tmp_path, authority_a, authority_b.ca)
    config = _gateway_config(monkeypatch, paths)
    credentials = GrpcServer(config)._create_server_credentials()
    assert credentials is not None
    server, port = await _serve(credentials)
    try:
        assert await _handshake_succeeds(port, authority_b, authority_b.ca) is False

        paths.certificate.write_bytes(authority_b.server_cert)
        paths.private_key.write_bytes(authority_b.server_key)

        assert await _handshake_succeeds(port, authority_b, authority_b.ca) is True
    finally:
        await server.stop(0)


def test_startup_refuses_credentials_when_the_certificate_is_not_pem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: _Authorities,
) -> None:
    """坏证书必须在启动期就被挡下。

    ``grpc.ssl_server_credentials`` 对垃圾字节照单全收（实测返回 ServerCredentials），
    于是 Gateway 会带着一张用不了的证书起来，直到 Worker 握手才集体失败。
    """
    authority_a = authorities.current
    paths = _write_material(tmp_path, authority_a, authority_a.ca)
    paths.certificate.write_bytes(b"this is not a certificate")
    config = _gateway_config(monkeypatch, paths)

    assert GrpcServer(config)._create_server_credentials() is None


def test_truncated_replacement_is_not_applied_and_reports_a_structured_code(
    tmp_path: Path,
    authorities: _Authorities,
) -> None:
    """运维半写入的 CA 文件不得顶掉当前材料，且必须留下结构化失败码。

    断言的是错误码常量而不是中文描述——描述会漂移，码不会。
    """
    authority_a = authorities.current
    paths = _write_material(tmp_path, authority_a, authority_a.ca)
    loader = TlsMaterialLoader(paths)
    loader.load()

    records: list[str] = []
    sink = logger.add(records.append, level="ERROR")
    try:
        paths.client_ca.write_bytes(b"-----BEGIN CERT")
        assert loader.certificate_configuration() is None
    finally:
        logger.remove(sink)

    assert any(TLS_MATERIAL_READ_FAILED in record for record in records)


def test_unchanged_material_is_reported_as_no_change(
    tmp_path: Path,
    authorities: _Authorities,
) -> None:
    """内容没变时必须返回 None，否则每次握手都白读一遍盘。"""
    authority_a = authorities.current
    paths = _write_material(tmp_path, authority_a, authority_a.ca)
    loader = TlsMaterialLoader(paths)
    loader.load()

    assert loader.certificate_configuration() is None


def test_repeated_callbacks_do_not_reread_unchanged_material_from_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: _Authorities,
) -> None:
    """材料没变时反复回调**一次盘都不该读**。

    上一条只断言"返回 None"，挡不住"每次都读满三个文件、比完内容再返回 None"这种
    实现——指纹存在的全部理由就是省掉那次读盘，判据必须落在读盘次数上。
    """
    paths = _write_material(tmp_path, authorities.current, authorities.current.ca)
    reads: list[str] = []
    read_bytes = Path.read_bytes

    def _counting(self: Path) -> bytes:
        reads.append(self.name)
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting)
    loader = TlsMaterialLoader(paths)
    loader.load()
    per_load = len(reads)

    for _ in range(REPEATED_HANDSHAKES):
        assert loader.certificate_configuration() is None
    assert len(reads) == per_load

    paths.client_ca.write_bytes(authorities.replacement.ca)
    assert loader.certificate_configuration() is not None
    assert len(reads) == per_load * 2


def test_successful_reload_reports_a_structured_code(
    tmp_path: Path,
    authorities: _Authorities,
) -> None:
    """换料成功也要留码：容器级校验靠数这条日志判断指纹短路有没有失效。"""
    paths = _write_material(tmp_path, authorities.current, authorities.current.ca)
    loader = TlsMaterialLoader(paths)
    loader.load()

    records: list[str] = []
    sink = logger.add(records.append, level="WARNING")
    try:
        paths.client_ca.write_bytes(authorities.replacement.ca)
        assert loader.certificate_configuration() is not None
    finally:
        logger.remove(sink)

    assert any(TLS_MATERIAL_RELOADED in record for record in records)
