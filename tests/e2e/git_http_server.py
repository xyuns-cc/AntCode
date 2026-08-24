"""Minimal smart HTTP Git server for isolated E2E repositories."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import ClassVar
from urllib.parse import urlsplit

GIT_BACKEND_TIMEOUT_SECONDS = 30
HTTP_SERVER_PORT = 8000


@dataclass(frozen=True)
class GitHttpConfig:
    root: Path
    backend: str
    # 本轮启动方生成的随机串。根路径原样回它，探活方才能证明"应答的是我起的那个
    # 进程"，而不只是"这个端口上有人应答"——两者以前长得一模一样。
    identity: str


def _request_path(raw_path: str) -> str:
    path = urlsplit(raw_path).path
    parts = PurePosixPath(path).parts
    if not path.startswith("/") or ".." in parts:
        raise ValueError("invalid Git HTTP path")
    return path


def _backend_env(handler: BaseHTTPRequestHandler, config: GitHttpConfig, content_length: int) -> dict[str, str]:
    parsed = urlsplit(handler.path)
    env = os.environ.copy()
    env.update(
        {
            "GIT_PROJECT_ROOT": str(config.root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": _request_path(handler.path),
            "QUERY_STRING": parsed.query,
            "REQUEST_METHOD": handler.command,
            "REQUEST_URI": handler.path,
            "SERVER_PROTOCOL": handler.request_version,
            "SERVER_NAME": handler.server.server_name,
            "SERVER_PORT": str(handler.server.server_port),
            "REMOTE_ADDR": handler.client_address[0],
            "CONTENT_TYPE": handler.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(content_length),
        }
    )
    return env


def _split_cgi_response(output: bytes) -> tuple[list[bytes], bytes]:
    header_block, separator, body = output.partition(b"\r\n\r\n")
    if not separator:
        header_block, separator, body = output.partition(b"\n\n")
    if not separator:
        raise RuntimeError("git http-backend returned no CGI headers")
    return header_block.splitlines(), body


def _response_metadata(header_lines: list[bytes]) -> tuple[int, list[tuple[str, str]]]:
    status_code = 200
    headers: list[tuple[str, str]] = []
    for raw_line in header_lines:
        name, separator, value = raw_line.decode("latin-1").partition(":")
        if not separator:
            continue
        if name.lower() == "status":
            status_code = int(value.strip().split()[0])
            continue
        headers.append((name.strip(), value.strip()))
    return status_code, headers


class GitHttpRequestHandler(BaseHTTPRequestHandler):
    config: ClassVar[GitHttpConfig]

    def do_GET(self) -> None:
        if _request_path(self.path) == "/":
            self._send_identity()
            return
        self._serve_backend(b"")

    def _send_identity(self) -> None:
        """根路径自报身份：本轮 identity、真正在服务的仓库根、进程号。

        以前这里回定长的 ``ok\\n``——任何占住同一端口的进程都给得出同样的应答，
        于是"端口通"被当成"我起来了"，上一轮遗留的孤儿能让整轮 E2E 对着别人的
        仓库树跑还报全过。回了这三样，探活方才有能证伪的判据，人排查时
        ``curl 根路径`` 也能直接看出占用者是谁、在服务哪棵树。
        """
        payload = json.dumps(
            {"identity": self.config.identity, "root": str(self.config.root), "pid": os.getpid()}
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        self._serve_backend(self.rfile.read(content_length))

    def _serve_backend(self, request_body: bytes) -> None:
        try:
            env = _backend_env(self, self.config, len(request_body))
            result = subprocess.run(
                [self.config.backend],
                input=request_body,
                env=env,
                check=False,
                capture_output=True,
                timeout=GIT_BACKEND_TIMEOUT_SECONDS,
            )
            headers, body = _split_cgi_response(result.stdout)
            status_code, response_headers = _response_metadata(headers)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.send_error(500, str(exc))
            return
        self.send_response(status_code)
        for name, value in response_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)


def _handler(config: GitHttpConfig) -> type[GitHttpRequestHandler]:
    class ConfiguredGitHttpRequestHandler(GitHttpRequestHandler):
        pass

    ConfiguredGitHttpRequestHandler.config = config
    return ConfiguredGitHttpRequestHandler


def _git_http_backend() -> Path:
    """问 git 自己要 backend 路径。

    它从不在 PATH 上（各发行版放 libexec，macOS 在 CommandLineTools 里），写死任一
    绝对路径都会在另一种环境上无声地退化成"服务起不来"。
    """
    exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, check=True).stdout.strip()
    backend = Path(exec_path) / "git-http-backend"
    if not backend.is_file():
        raise RuntimeError(f"git-http-backend is not installed: {backend}")
    return backend


def serve(root: Path, host: str, port: int, *, identity: str) -> None:
    config = GitHttpConfig(root=root.resolve(), backend=str(_git_http_backend()), identity=identity)
    config.root.mkdir(parents=True, exist_ok=True)
    with ThreadingHTTPServer((host, port), _handler(config)) as server:
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=HTTP_SERVER_PORT)
    # 必填而不是自动生成：identity 的唯一用处是让**启动方**核对应答方，
    # 服务端自己编一个等于谁也证明不了。
    parser.add_argument("--identity", required=True)
    args = parser.parse_args()
    serve(args.root, args.host, args.port, identity=args.identity)


if __name__ == "__main__":
    main()
