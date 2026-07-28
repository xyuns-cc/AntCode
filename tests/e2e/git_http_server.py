"""Minimal smart HTTP Git server for isolated E2E repositories."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from dataclasses import dataclass
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
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        self._serve_backend(b"")

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


def serve(root: Path, host: str, port: int) -> None:
    backend = shutil.which("git-http-backend") or "/usr/lib/git-core/git-http-backend"
    if not Path(backend).is_file():
        raise RuntimeError("git-http-backend is not installed")
    config = GitHttpConfig(root=root.resolve(), backend=backend)
    config.root.mkdir(parents=True, exist_ok=True)
    with ThreadingHTTPServer((host, port), _handler(config)) as server:
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=HTTP_SERVER_PORT)
    args = parser.parse_args()
    serve(args.root, args.host, args.port)


if __name__ == "__main__":
    main()
