"""Gateway listen-address helpers."""

from __future__ import annotations


def grpc_listen_address(host: str, port: int) -> str:
    normalized = host.strip()
    if not normalized:
        raise ValueError("GRPC_HOST 不能为空")
    if normalized.startswith("[") and normalized.endswith("]"):
        return f"{normalized}:{port}"
    if ":" in normalized:
        return f"[{normalized}]:{port}"
    return f"{normalized}:{port}"


__all__ = ["grpc_listen_address"]
