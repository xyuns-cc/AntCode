"""Verify production HTTPS and Gateway mTLS before release E2E scenarios."""

from __future__ import annotations

import argparse
import socket
import ssl
from pathlib import Path

import grpc
import httpx

HTTP_TIMEOUT_SECONDS = 15.0
GRPC_TIMEOUT_SECONDS = 10.0
HTTP_PERMANENT_REDIRECT_STATUS = 308
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--client-cert", type=Path, required=True)
    parser.add_argument("--client-key", type=Path, required=True)
    parser.add_argument("--gateway-host", default="localhost")
    parser.add_argument("--gateway-port", type=int, default=15051)
    parser.add_argument("--https-origin", default="https://localhost")
    parser.add_argument("--http-origin", default="http://localhost")
    return parser.parse_args()


def _verify_https(args: argparse.Namespace) -> None:
    with httpx.Client(verify=str(args.ca), timeout=HTTP_TIMEOUT_SECONDS) as client:
        redirect = client.get(args.http_origin, follow_redirects=False)
        expected_location = f"{args.https_origin.rstrip('/')}/"
        if (
            redirect.status_code != HTTP_PERMANENT_REDIRECT_STATUS
            or not redirect.headers.get("location", "") == expected_location
        ):
            raise RuntimeError("production HTTP endpoint did not redirect to the exact HTTPS origin")
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


def main() -> None:
    args = _arguments()
    _verify_https(args)
    _verify_tls_versions(args)
    _verify_mtls(args)


if __name__ == "__main__":
    main()
