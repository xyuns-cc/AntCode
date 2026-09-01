"""Verify production HTTPS and Gateway mTLS before release E2E scenarios."""

from __future__ import annotations

import argparse
import socket
import ssl
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

import grpc
import httpx

from scripts.release_e2e_endpoints import release_endpoints
from scripts.release_e2e_environment import RUNNER_HOST

HTTP_TIMEOUT_SECONDS = 15.0
GRPC_TIMEOUT_SECONDS = 10.0
HTTP_PERMANENT_REDIRECT_STATUS = 308
#: 合成冒充身份的后缀；与证书 CN 只差这一段，用来证明绑定不是前缀/后缀宽松匹配。
IMPOSTOR_SUFFIX = "-impostor"
#: 每个安全头必须**恰好**出现一次。web_api 中间件、frontend nginx 与公网反代三层
#: 都会设这组头，nginx 的 add_header 只追加不覆盖，直接透传会让客户端收到三份
#: （真机实测）。RFC 7034 §2.1 明确多值 X-Frame-Options 无效、浏览器可直接忽略整条头，
#: 所以"重复出现"必须当失败处理，而不是宽松地"存在即可"。
EXACTLY_ONCE_SECURITY_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}


def _arguments() -> argparse.Namespace:
    """origin 与端口一律从本轮 `production.env` 推导，本脚本不带任何端口默认值。

    带默认值时 run-gateway-e2e.sh 从来没传过 origin，端口一重映射判据就打到 :443 / :80
    上——共享测试机上那可能是别人的栈，而它设的正是同一组安全头，"连通 + 头对"因此
    无法区分被验对象是谁。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", type=Path, required=True, help="本轮 production.env")
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--client-cert", type=Path, required=True)
    parser.add_argument("--client-key", type=Path, required=True)
    parser.add_argument("--gateway-host", default=RUNNER_HOST)
    parser.add_argument("--worker-id", required=True, help="--client-cert 的 CN，即该证书的合法主体")
    parser.add_argument("--peer-worker-id", default="", help="另一个已注册 Worker 的 ID；多 Worker 拓扑下必给")
    parsed = parser.parse_args()
    return argparse.Namespace(**vars(parsed), **asdict(release_endpoints(parsed.environment)))


def _verify_redirect(response: httpx.Response, https_origin: str) -> None:
    """308 目标由 nginx 的 `$host` 拼出，而 `$host` 已被 nginx 剥掉端口。

    发布 E2E 允许把 443/80 重映射到别的宿主端口，那是测试机上的编排细节，不属于生产
    契约，所以这里只钉死"跳到同一主机的 HTTPS 且路径原样"。端口由紧随其后那次 ready
    请求负责证明：它连的是本轮真正发布的端口，并且用本轮一次性 CA 校验服务端。
    """
    expected = f"https://{urlsplit(https_origin).hostname}/"
    if response.status_code != HTTP_PERMANENT_REDIRECT_STATUS or response.headers.get("location") != expected:
        raise RuntimeError(
            "production HTTP endpoint did not redirect to the exact HTTPS origin: "
            f"status={response.status_code} location={response.headers.get('location')!r} expected={expected!r}"
        )


def _verify_https(args: argparse.Namespace) -> None:
    with httpx.Client(verify=str(args.ca), timeout=HTTP_TIMEOUT_SECONDS) as client:
        _verify_redirect(client.get(args.http_origin, follow_redirects=False), args.https_origin)
        ready = client.get(f"{args.https_origin}/api/v1/health/ready")
        ready.raise_for_status()
        _verify_security_headers(ready)


def _verify_security_headers(response: httpx.Response) -> None:
    for name, expected in EXACTLY_ONCE_SECURITY_HEADERS.items():
        values = response.headers.get_list(name)
        if values != [expected]:
            raise RuntimeError(f"production HTTPS security header must appear exactly once: {name}={values}")


def _tls_context(args: argparse.Namespace, version: ssl.TLSVersion) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(args.ca))
    context.minimum_version = version
    context.maximum_version = version
    context.load_cert_chain(certfile=str(args.client_cert), keyfile=str(args.client_key))
    if version in {ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1_1}:
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
    return context


def _negotiate_tls(args: argparse.Namespace, version: ssl.TLSVersion) -> str | None:
    context = _tls_context(args, version)
    with socket.create_connection((args.gateway_host, args.gateway_port), timeout=GRPC_TIMEOUT_SECONDS) as raw:
        with context.wrap_socket(raw, server_hostname=args.gateway_host) as secured:
            return secured.version()


def _verify_tls_versions(args: argparse.Namespace) -> None:
    allowed = (
        (ssl.TLSVersion.TLSv1_2, "TLSv1.2"),
        (ssl.TLSVersion.TLSv1_3, "TLSv1.3"),
    )
    for version, expected in allowed:
        if _negotiate_tls(args, version) != expected:
            raise RuntimeError(f"Gateway did not negotiate required {expected}")
    for version in (ssl.TLSVersion.TLSv1, ssl.TLSVersion.TLSv1_1):
        try:
            negotiated = _negotiate_tls(args, version)
        except (ConnectionResetError, ssl.SSLError):
            continue
        raise RuntimeError(f"Gateway accepted forbidden TLS version: {negotiated}")


def _channel_credentials(args: argparse.Namespace, *, with_client: bool) -> grpc.ChannelCredentials:
    root = args.ca.read_bytes()
    if not with_client:
        return grpc.ssl_channel_credentials(root_certificates=root)
    return grpc.ssl_channel_credentials(
        root_certificates=root,
        private_key=args.client_key.read_bytes(),
        certificate_chain=args.client_cert.read_bytes(),
    )


def _channel_becomes_ready(args: argparse.Namespace, *, with_client: bool) -> bool:
    target = f"{args.gateway_host}:{args.gateway_port}"
    channel = grpc.secure_channel(target, _channel_credentials(args, with_client=with_client))
    try:
        grpc.channel_ready_future(channel).result(timeout=GRPC_TIMEOUT_SECONDS)
        return True
    except grpc.FutureTimeoutError:
        return False
    finally:
        channel.close()


def _verify_mtls(args: argparse.Namespace) -> None:
    if _channel_becomes_ready(args, with_client=False):
        raise RuntimeError("Gateway accepted a TLS channel without a client certificate")
    if not _channel_becomes_ready(args, with_client=True):
        raise RuntimeError("Gateway rejected the trusted Worker client certificate")


def _capabilities_status(args: argparse.Namespace, claimed_worker_id: str) -> grpc.StatusCode | None:
    """用本地客户端证书连 Gateway，声明 ``claimed_worker_id``，返回失败状态码（成功为 None）。

    mTLS 链路上 Worker **不带 api_key**（transport/factory_components.gateway_auth_method：
    有客户端证书就走 mtls），x-worker-id 只是客户端的自我声明，唯一的身份凭据就是
    证书本身。所以"证书 CN 与声明主体是否强绑定"就是这条链路的全部认证强度。
    GetCapabilities 是无副作用且必经认证的 RPC（不在 AUTH_EXEMPT_METHODS 白名单里）。
    """
    from antcode_contracts import control_pb2, control_pb2_grpc

    target = f"{args.gateway_host}:{args.gateway_port}"
    channel = grpc.secure_channel(target, _channel_credentials(args, with_client=True))
    try:
        stub = control_pb2_grpc.ControlServiceStub(channel)
        stub.GetCapabilities(
            control_pb2.CapabilitiesRequest(worker_id=claimed_worker_id),
            metadata=(("x-worker-id", claimed_worker_id),),
            timeout=GRPC_TIMEOUT_SECONDS,
        )
        return None
    except grpc.RpcError as error:
        return error.code()
    finally:
        channel.close()


def _verify_certificate_identity_binding(args: argparse.Namespace) -> None:
    """CA 签发的合法证书不得冒充**别的** Worker——否则任何一台 Worker 都能顶替全部同伴。

    合成身份恒验（证明绑定生效），另有真实同伴 ID 时再验一次真实冒充：只验合成 ID
    可能被"该 Worker 不存在"这类前置校验挡下，从而掩盖 CN 绑定本身是否真的生效。
    """
    own = _capabilities_status(args, args.worker_id)
    if own is not None:
        raise RuntimeError(f"Gateway rejected a Worker presenting its own certificate identity: {own}")
    for claimed in (f"{args.worker_id}{IMPOSTOR_SUFFIX}", args.peer_worker_id):
        if not claimed or claimed == args.worker_id:
            continue
        status = _capabilities_status(args, claimed)
        if status != grpc.StatusCode.PERMISSION_DENIED:
            raise RuntimeError(f"Gateway let certificate CN={args.worker_id} act as {claimed}: status={status}")


def main() -> None:
    args = _arguments()
    _verify_https(args)
    _verify_tls_versions(args)
    _verify_mtls(args)
    _verify_certificate_identity_binding(args)


if __name__ == "__main__":
    main()
