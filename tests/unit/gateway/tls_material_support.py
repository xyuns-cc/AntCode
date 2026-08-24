"""``tls_material`` 两个测试文件共用的 PKI 与握手脚手架。

轮换（真实握手证明材料换得动）与变更检测（证明"变没变"这个判据本身是对的）是两类
不同的断言，各占一个文件；PKI 生成很贵（每套 CA + 两张叶子证书都是 RSA-2048），
所以在这里做一次并缓存，两个文件共享同一套材料。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import grpc
from antcode_gateway.tls_material import TlsMaterialPaths
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from scripts.release_e2e_pki import _create_ca, _create_leaf, _pem_cert, _pem_key

HANDSHAKE_TIMEOUT_SECONDS = 5


class Authority:
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
class Authorities:
    """成对下发，免得每个用例都要多收一个 fixture 撞上位置参数上限。"""

    current: Authority
    replacement: Authority


@lru_cache(maxsize=1)
def build_authorities() -> Authorities:
    return Authorities(current=Authority("worker-a"), replacement=Authority("worker-b"))


def write_material(root: Path, serving: Authority, trusted_ca: bytes) -> TlsMaterialPaths:
    root.mkdir(parents=True, exist_ok=True)
    (root / "server.crt").write_bytes(serving.server_cert)
    (root / "server.key").write_bytes(serving.server_key)
    (root / "ca.crt").write_bytes(trusted_ca)
    return TlsMaterialPaths(
        certificate=root / "server.crt",
        private_key=root / "server.key",
        client_ca=root / "ca.crt",
    )


def rewrite_in_place_keeping_stat(path: Path, value: bytes) -> None:
    """就地改写内容，并把 ``(st_ino, st_mtime_ns, st_size)`` 三元组原样保留。

    这是"stat 说没变、内容其实变了"的**确定性**构造。实机上有两条路径会落进这个
    形态，都实测过（ext4 与 overlayfs 表现一致）：

    - 写方保留时间戳：``cp -p`` / ``rsync -t`` / ``tar -x`` / ``touch -r``，mtime 被
      还原成旧值，只有 ctime 变；
    - 同一个时间戳 tick 内改写两次：内核给 inode 打的是粗粒度时间戳（实测 ext4 为
      4ms），tick 内的第二次写连 ctime 都不变——所以把 ctime 加进三元组也救不了。

    用 ``os.utime`` 精确复现这个形态，而不是靠 sleep 去赌 tick 边界：判据要确定。
    """
    before = path.stat()
    if len(value) != before.st_size:
        raise ValueError("就地改写必须同尺寸，否则 st_size 就把变更暴露了")
    with path.open("r+b") as handle:
        handle.seek(0)
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


async def handshake_succeeds(port: int, client: Authority, server_ca: bytes) -> bool:
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


async def serve(credentials: grpc.ServerCredentials) -> tuple[grpc.aio.Server, int]:
    server = grpc.aio.server()
    health_pb2_grpc.add_HealthServicer_to_server(health.HealthServicer(), server)
    port = server.add_secure_port("localhost:0", credentials)
    await server.start()
    return server, port


__all__ = [
    "Authorities",
    "Authority",
    "build_authorities",
    "handshake_succeeds",
    "rewrite_in_place_keeping_stat",
    "serve",
    "write_material",
]
