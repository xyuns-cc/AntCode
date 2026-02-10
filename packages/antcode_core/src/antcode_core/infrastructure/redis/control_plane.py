"""Redis 控制平面协议与 Key 规范。"""

from __future__ import annotations

import json
from typing import Any, Mapping

from antcode_core.common.config import settings


def redis_namespace(namespace: str | None = None) -> str:
    """获取 Redis 命名空间。"""
    value = (namespace or settings.REDIS_NAMESPACE or "antcode").strip()
    return value or "antcode"


def task_ready_stream(worker_id: str, namespace: str | None = None) -> str:
    """任务 ready stream key。"""
    return f"{redis_namespace(namespace)}:task:ready:{worker_id}"


def task_result_stream(namespace: str | None = None) -> str:
    """任务结果 stream key。"""
    return f"{redis_namespace(namespace)}:task:result"


def log_stream_key(run_id: str, namespace: str | None = None) -> str:
    """运行日志 stream key。"""
    return f"{redis_namespace(namespace)}:log:stream:{run_id}"


def log_chunk_stream_key(run_id: str, namespace: str | None = None) -> str:
    """运行日志分片 stream key。"""
    return f"{redis_namespace(namespace)}:log:chunk:{run_id}"


def log_stream_pattern(namespace: str | None = None) -> str:
    """运行日志 stream 扫描模式。"""
    return f"{redis_namespace(namespace)}:log:stream:*"


def log_chunk_stream_pattern(namespace: str | None = None) -> str:
    """运行日志分片 stream 扫描模式。"""
    return f"{redis_namespace(namespace)}:log:chunk:*"


def control_stream(worker_id: str, namespace: str | None = None) -> str:
    """控制通道 stream key。"""
    return f"{redis_namespace(namespace)}:control:{worker_id}"


def control_global_stream(namespace: str | None = None) -> str:
    """全局控制通道 stream key。"""
    return f"{redis_namespace(namespace)}:control:global"


def control_reply_stream(request_id: str, namespace: str | None = None) -> str:
    """控制结果回复 stream key。"""
    return f"{redis_namespace(namespace)}:control:reply:{request_id}"


def worker_heartbeat_key(worker_id: str, namespace: str | None = None) -> str:
    """Worker 心跳 key。"""
    return f"{redis_namespace(namespace)}:heartbeat:{worker_id}"


def worker_group(namespace: str | None = None) -> str:
    """任务消费组名称。"""
    return f"{redis_namespace(namespace)}-workers"


def control_group(namespace: str | None = None) -> str:
    """控制消费组名称。"""
    return f"{redis_namespace(namespace)}-control"


def direct_register_proof_key(worker_id: str, namespace: str | None = None) -> str:
    """Worker Direct 注册证明 key。"""
    return f"{redis_namespace(namespace)}:direct:register:{worker_id}"


def worker_install_key_fail_counter_key(
    key: str,
    source: str,
    namespace: str | None = None,
) -> str:
    """安装 Key 失败计数器 key。"""
    return f"{redis_namespace(namespace)}:worker:install-key:fail:{key}:{source}"


def worker_install_key_block_key(
    key: str,
    source: str,
    namespace: str | None = None,
) -> str:
    """安装 Key 来源封禁 key。"""
    return f"{redis_namespace(namespace)}:worker:install-key:block:{key}:{source}"


def worker_install_key_claim_key(key: str, namespace: str | None = None) -> str:
    """安装 Key 来源声明 key。"""
    return f"{redis_namespace(namespace)}:worker:install-key:claim:{key}"


def worker_install_key_nonce_key(
    key: str,
    request_nonce: str,
    namespace: str | None = None,
) -> str:
    """安装 Key 防重放 nonce key。"""
    return f"{redis_namespace(namespace)}:worker:install-key:nonce:{key}:{request_nonce}"


def worker_install_key_meta_key(key: str, namespace: str | None = None) -> str:
    """安装 Key 元信息 key。"""
    return f"{redis_namespace(namespace)}:worker:install-key:meta:{key}"


def build_cancel_control_payload(run_id: str, reason: str = "", task_id: str | None = None) -> dict[str, str]:
    """构建取消任务控制指令。"""
    payload = {
        "control_type": "cancel",
        "task_id": task_id or run_id,
        "run_id": run_id,
    }
    if reason:
        payload["reason"] = reason
    return payload


def build_config_update_control_payload(config: Mapping[str, Any]) -> dict[str, str]:
    """构建配置更新控制指令。"""
    return {
        "control_type": "config_update",
        "config": json.dumps(dict(config), ensure_ascii=False),
    }


def build_runtime_manage_control_payload(
    action: str,
    request_id: str,
    reply_stream: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """构建运行时管理控制指令。"""
    return {
        "control_type": "runtime_manage",
        "action": action,
        "request_id": request_id,
        "reply_stream": reply_stream,
        "payload": json.dumps(dict(payload or {}), ensure_ascii=False),
    }


def decode_stream_payload(data: Mapping[Any, Any]) -> dict[str, Any]:
    """解码 Redis Stream payload，并解析 JSON 字段。"""
    decoded: dict[str, Any] = {}
    for key, value in data.items():
        parsed_key = key.decode() if isinstance(key, bytes) else str(key)
        parsed_value = value.decode() if isinstance(value, bytes) else value
        decoded[parsed_key] = parsed_value

    for json_key in ("config", "payload", "data", "params", "environment", "metrics"):
        if json_key in decoded and isinstance(decoded[json_key], str):
            raw = decoded[json_key]
            if not raw:
                continue
            try:
                decoded[json_key] = json.loads(raw)
            except Exception:
                continue

    return decoded


__all__ = [
    "redis_namespace",
    "task_ready_stream",
    "task_result_stream",
    "log_stream_key",
    "log_chunk_stream_key",
    "log_stream_pattern",
    "log_chunk_stream_pattern",
    "control_stream",
    "control_global_stream",
    "control_reply_stream",
    "worker_heartbeat_key",
    "worker_group",
    "control_group",
    "direct_register_proof_key",
    "worker_install_key_fail_counter_key",
    "worker_install_key_block_key",
    "worker_install_key_claim_key",
    "worker_install_key_nonce_key",
    "worker_install_key_meta_key",
    "build_cancel_control_payload",
    "build_config_update_control_payload",
    "build_runtime_manage_control_payload",
    "decode_stream_payload",
]
