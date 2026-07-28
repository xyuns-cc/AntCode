"""Executable OpenSSH ProxyCommand helper for the local pinned Git relay."""

from __future__ import annotations

import os
import selectors
import socket
import sys

BUFFER_BYTES = 64 * 1024
LOOPBACK_HOST = "127.0.0.1"


def proxy_stdio(port: int, token: str) -> None:
    with socket.create_connection((LOOPBACK_HOST, port)) as relay:
        relay.sendall(f"{token}\n".encode("ascii"))
        _forward_stdio(relay)


def _forward_stdio(relay: socket.socket) -> None:
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.buffer, selectors.EVENT_READ, "stdin")
    selector.register(relay, selectors.EVENT_READ, "relay")
    with selector:
        while selector.get_map():
            for key, _event_mask in selector.select():
                if key.data == "stdin":
                    if not _read_stdin(selector, relay):
                        continue
                elif not _read_relay(relay):
                    return


def _read_stdin(selector: selectors.BaseSelector, relay: socket.socket) -> bool:
    data = os.read(sys.stdin.fileno(), BUFFER_BYTES)
    if data:
        relay.sendall(data)
        return True
    selector.unregister(sys.stdin.buffer)
    relay.shutdown(socket.SHUT_WR)
    return False


def _read_relay(relay: socket.socket) -> bool:
    data = relay.recv(BUFFER_BYTES)
    if not data:
        return False
    _write_stdout(data)
    return True


def _write_stdout(data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(sys.stdout.fileno(), view)
        view = view[written:]


def main() -> int:
    if len(sys.argv) != 3:
        raise ValueError("usage: git_ssh_proxy PORT TOKEN")
    proxy_stdio(int(sys.argv[1]), sys.argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
