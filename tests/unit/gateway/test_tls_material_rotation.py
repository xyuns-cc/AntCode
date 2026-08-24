"""Gateway 的 mTLS 材料必须能在不重启的前提下轮换 / 吊销。

以前 ``GrpcServer._create_server_credentials`` 一次性把证书读进 gRPC C core，
之后再也不看磁盘：证书到期或某张 CA 被攻陷，唯一手段是重启 Gateway，而重启会
同时打断全部 Worker 的数据面长连接。这里用真实 TLS 握手证明"改盘即生效"。

判据本身（"变没变"怎么算）钉在 ``test_tls_material_change_detection``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from antcode_gateway.config import GatewayConfig
from antcode_gateway.server import GrpcServer
from antcode_gateway.tls_material import TlsMaterialPaths

from tests.unit.gateway.tls_material_support import (
    Authorities,
    build_authorities,
    handshake_succeeds,
    rewrite_in_place_keeping_stat,
    serve,
    write_material,
)


@pytest.fixture(scope="module")
def authorities() -> Authorities:
    return build_authorities()


def _gateway_config(monkeypatch: pytest.MonkeyPatch, paths: TlsMaterialPaths) -> GatewayConfig:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("GRPC_TLS_CERT_PATH", str(paths.certificate))
    monkeypatch.setenv("GRPC_TLS_KEY_PATH", str(paths.private_key))
    monkeypatch.setenv("GRPC_TLS_CA_PATH", str(paths.client_ca))
    return GatewayConfig()


@pytest.mark.asyncio
async def test_rotating_the_ca_bundle_on_disk_is_honoured_without_restarting_the_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: Authorities,
) -> None:
    """把 CA-A 换成 CA-B 后：B 立刻可连，A 立刻被拒——全程不重启。

    钉死的是两个方向：新 CA 生效（轮换）与旧 CA 失效（吊销）。凭证一次性读盘时
    两条断言都会红。
    """
    authority_a, authority_b = authorities.current, authorities.replacement
    paths = write_material(tmp_path, authority_a, authority_a.ca)
    config = _gateway_config(monkeypatch, paths)
    credentials = GrpcServer(config)._create_server_credentials()
    assert credentials is not None
    server, port = await serve(credentials)
    try:
        assert await handshake_succeeds(port, authority_a, authority_a.ca) is True
        assert await handshake_succeeds(port, authority_b, authority_a.ca) is False

        paths.client_ca.write_bytes(authority_b.ca)

        assert await handshake_succeeds(port, authority_b, authority_a.ca) is True
        assert await handshake_succeeds(port, authority_a, authority_a.ca) is False
    finally:
        await server.stop(0)


@pytest.mark.asyncio
async def test_revoking_a_ca_by_stat_identical_rewrite_is_honoured_without_restarting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: Authorities,
) -> None:
    """运维就地改写 CA bundle 吊销一张 CA，且 stat 三元组恰好没变 —— 也必须生效。

    这是本用例存在的唯一理由：按 ``(st_ino, st_mtime_ns, st_size)`` 判变化时，
    fetcher 会认定"材料没变"、返回 None、**沿用旧 CA bundle**，于是被吊销的 CA
    继续被信任，而且一条日志都不会有。安全后果是静默的，所以判据必须落在
    "旧 CA 真的连不上了"这个方向上，而不只是"新 CA 能连"。
    """
    authority_a, authority_b = authorities.current, authorities.replacement
    paths = write_material(tmp_path, authority_a, authority_a.ca)
    config = _gateway_config(monkeypatch, paths)
    credentials = GrpcServer(config)._create_server_credentials()
    assert credentials is not None
    server, port = await serve(credentials)
    try:
        assert await handshake_succeeds(port, authority_a, authority_a.ca) is True

        rewrite_in_place_keeping_stat(paths.client_ca, authority_b.ca)

        assert await handshake_succeeds(port, authority_a, authority_a.ca) is False
        assert await handshake_succeeds(port, authority_b, authority_a.ca) is True
    finally:
        await server.stop(0)


@pytest.mark.asyncio
async def test_expired_style_server_certificate_swap_is_honoured_without_restarting_the_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: Authorities,
) -> None:
    """服务端证书换成 B 家的之后，只信任 CA-B 的客户端才校验得过服务端身份。

    这条覆盖"服务端证书到期换新"这一路：静态凭证下服务端永远出示旧证书，
    ``handshake_succeeds(..., authority_b.ca)`` 恒 False。
    """
    authority_a, authority_b = authorities.current, authorities.replacement
    paths = write_material(tmp_path, authority_a, authority_b.ca)
    config = _gateway_config(monkeypatch, paths)
    credentials = GrpcServer(config)._create_server_credentials()
    assert credentials is not None
    server, port = await serve(credentials)
    try:
        assert await handshake_succeeds(port, authority_b, authority_b.ca) is False

        paths.certificate.write_bytes(authority_b.server_cert)
        paths.private_key.write_bytes(authority_b.server_key)

        assert await handshake_succeeds(port, authority_b, authority_b.ca) is True
    finally:
        await server.stop(0)


def test_startup_refuses_credentials_when_the_certificate_is_not_pem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: Authorities,
) -> None:
    """坏证书必须在启动期就被挡下。

    ``grpc.ssl_server_credentials`` 对垃圾字节照单全收（实测返回 ServerCredentials），
    于是 Gateway 会带着一张用不了的证书起来，直到 Worker 握手才集体失败。
    """
    authority_a = authorities.current
    paths = write_material(tmp_path, authority_a, authority_a.ca)
    paths.certificate.write_bytes(b"this is not a certificate")
    config = _gateway_config(monkeypatch, paths)

    assert GrpcServer(config)._create_server_credentials() is None
