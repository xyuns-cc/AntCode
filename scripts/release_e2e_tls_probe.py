"""Probes used by the container-level Gateway TLS hot-reload gate.

三类观测各自解决一个"假绿"形状：

- ``container_identity``：证明**没重启**。只看"新证书能连"分不出热更新与悄悄重启，
  PID / 启动时间 / 重启次数三项同时不变才排除得掉。
- ``served_certificate``：证明服务端**当前出示的就是刚写进盘的那张**。裸 ``ssl``
  每次都开新 socket，必然是一次全新握手——热更新只对新连接生效，复用连接会把
  "没换成"看成"换成了"。
- ``reload_count``：数容器日志里 ``TLS_MATERIAL_RELOADED`` 的条数。材料没变时它
  一条都不该多，这是"指纹短路"在容器级唯一可观测的痕迹。
"""

from __future__ import annotations

import hashlib
import secrets
import socket
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path

import grpc
from antcode_gateway.tls_material import TLS_MATERIAL_RELOADED

HANDSHAKE_TIMEOUT_SECONDS = 10.0
#: 与 release_e2e_pki.CONTAINER_FILE_MODE 一致：容器里的 appuser 必须读得到。
MATERIAL_FILE_MODE = 0o444


@dataclass(frozen=True, slots=True)
class ContainerIdentity:
    """ "这个进程从头到尾没被换过"的三重证据。"""

    pid: str
    started_at: str
    restart_count: str


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    """一个客户端的 mTLS 材料；``trusted_ca`` 用来校验**服务端**。"""

    certificate: Path
    private_key: Path
    trusted_ca: Path


def _docker(*arguments: str) -> str:
    completed = subprocess.run(("docker", *arguments), capture_output=True, text=True, check=True)
    # loguru 写 stderr，docker logs 两条流都要收，否则重载日志一条都数不到。
    return completed.stdout + completed.stderr


def container_identity(container: str) -> ContainerIdentity:
    template = "{{.State.Pid}}|{{.State.StartedAt}}|{{.RestartCount}}"
    pid, started_at, restart_count = _docker("inspect", "--format", template, container).strip().split("|")
    return ContainerIdentity(pid=pid, started_at=started_at, restart_count=restart_count)


def reload_count(container: str) -> int:
    """容器日志里"材料真的被重新读盘并换给 gRPC"的累计次数。

    匹配的是结构化码常量，不是中文描述——描述会漂移，码不会。要求 Gateway 的日志
    级别不高于 WARNING，否则这条痕迹根本不落盘，计数会恒为 0 并让校验直接失败。
    """
    return _docker("logs", container).count(TLS_MATERIAL_RELOADED)


def served_certificate(endpoint: tuple[str, int], client: ClientIdentity) -> str:
    """新开一条 TLS 连接，返回服务端**本次**出示的证书指纹（sha256/DER）。"""
    context = ssl.create_default_context(cafile=str(client.trusted_ca))
    context.load_cert_chain(certfile=str(client.certificate), keyfile=str(client.private_key))
    with socket.create_connection(endpoint, timeout=HANDSHAKE_TIMEOUT_SECONDS) as raw:
        with context.wrap_socket(raw, server_hostname=endpoint[0]) as secured:
            presented = secured.getpeercert(binary_form=True)
    if presented is None:
        raise RuntimeError("TLS handshake completed without a peer certificate")
    return hashlib.sha256(presented).hexdigest()


def channel_becomes_ready(endpoint: tuple[str, int], client: ClientIdentity) -> bool:
    """客户端证书**是否被服务端接住**。

    用 gRPC 而不是裸 ``ssl``：TLS1.3 下客户端证书是握手之后才被服务端校验的，
    ``do_handshake()`` 返回成功并不代表没被拒；通道进入 READY 才是。
    """
    credentials = grpc.ssl_channel_credentials(
        root_certificates=client.trusted_ca.read_bytes(),
        private_key=client.private_key.read_bytes(),
        certificate_chain=client.certificate.read_bytes(),
    )
    host, port = endpoint
    # 全局 subchannel 池会把参数相同的通道并到同一条**已建立**的连接上，那条连接
    # 用的还是换料之前的材料，正负两个方向都会读成假结果。
    channel = grpc.secure_channel(f"{host}:{port}", credentials, options=(("grpc.use_local_subchannel_pool", 1),))
    try:
        grpc.channel_ready_future(channel).result(timeout=HANDSHAKE_TIMEOUT_SECONDS)
        return True
    except grpc.FutureTimeoutError:
        return False
    finally:
        channel.close()


def replace_material(path: Path, value: bytes) -> None:
    """原子替换一份材料。

    换 inode 而不是就地改写：不会让 Gateway 读到半截文件。变更检测本身按内容哈希
    判，就地改写也不会漏，这里选原子替换纯粹是为了排除"读到半截"这个干扰变量。
    容器侧绑定的是**目录**，宿主换掉的文件立刻可见。
    """
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
    temporary.write_bytes(value)
    temporary.chmod(MATERIAL_FILE_MODE)
    temporary.replace(path)


__all__ = [
    "ClientIdentity",
    "ContainerIdentity",
    "channel_becomes_ready",
    "container_identity",
    "reload_count",
    "replace_material",
    "served_certificate",
]
