"""Redis 控制平面协议与 Key 规范。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from antcode_core.common.settings_ref import current_settings
from antcode_core.infrastructure.redis.runtime_control_keys import (
    require_runtime_control_request_id,
    runtime_control_request_id,
    validate_worker_key_segment,
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Redis Stream JSON 包含非标准常量: {value}")


def redis_namespace(namespace: str | None = None) -> str:
    """获取 Redis 命名空间。"""
    value = (namespace or current_settings().REDIS_NAMESPACE or "antcode").strip()
    return value or "antcode"


def task_ready_stream(worker_id: str, namespace: str | None = None) -> str:
    """任务 ready stream key，与权威 Lease 共享 Redis Cluster slot。"""
    return f"{{{redis_namespace(namespace)}}}:task:ready:{worker_id}"


def scheduler_dispatch_fencing_key(namespace: str | None = None) -> str:
    """Current Master epoch in the ready-stream Redis Cluster slot."""
    return f"{{{redis_namespace(namespace)}}}:fencing:dispatch:master"


def task_result_stream(namespace: str | None = None) -> str:
    """任务结果 stream key。"""
    return f"{redis_namespace(namespace)}:task:result"


def log_stream_key(run_id: str, namespace: str | None = None) -> str:
    """运行日志 stream key。"""
    return f"{redis_namespace(namespace)}:log:stream:{run_id}"


def log_ingest_stream_key(namespace: str | None = None) -> str:
    """日志落库 stream，与 Lease/ownership 共享 Cluster hash slot。"""
    return f"{{{redis_namespace(namespace)}}}:log:ingest"


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
    """控制通道 stream key，与权威 Lease 共享 Redis Cluster slot。"""
    return f"{{{redis_namespace(namespace)}}}:control:{worker_id}"


# E5: consumer group 命名带 namespace，避免 REDIS_NAMESPACE 改动后
# master 侧硬编码 "antcode-workers" 与 worker 侧 "{ns}-workers" 分叉。
def worker_consumer_group(namespace: str | None = None) -> str:
    """Worker 拉取任务 ready stream 的 consumer group 名。"""
    return f"{redis_namespace(namespace)}-workers"


def result_consumer_group(namespace: str | None = None) -> str:
    """Master ingester 消费 task result stream 的 consumer group 名。"""
    return f"{redis_namespace(namespace)}-results"


def log_ingest_consumer_group(namespace: str | None = None) -> str:
    """Master ingester 消费 log ingest stream 的 consumer group 名。"""
    return f"{redis_namespace(namespace)}-log-ingest"


def control_global_stream(namespace: str | None = None) -> str:
    """可信 Gateway 使用的共享广播 stream；Direct Worker 不得访问。"""
    return f"{{{redis_namespace(namespace)}}}:control:global"


def control_reply_stream(request_id: str, namespace: str | None = None) -> str:
    """控制结果回复 stream key。"""
    return f"{redis_namespace(namespace)}:control:reply:{request_id}"


def runtime_control_settlement_key(
    worker_id: str,
    identity: str,
    namespace: str | None = None,
) -> str:
    """Build a hashed Direct settlement key inside one Worker's ACL scope."""
    worker_segment = validate_worker_key_segment(worker_id)
    if not identity:
        raise ValueError("运行时控制 settlement identity 不能为空")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{{{redis_namespace(namespace)}}}:control:settlement:{worker_segment}:{digest}"


def cancel_tombstone_key(worker_id: str, run_id: str, namespace: str | None = None) -> str:
    """Build a cancel tombstone key inside one Worker's ACL scope."""
    worker_segment = validate_worker_key_segment(worker_id)
    if not run_id:
        raise ValueError("取消墓碑 run_id 不能为空")
    return f"{{{redis_namespace(namespace)}}}:cancel:tombstone:{worker_segment}:{run_id}"


def worker_heartbeat_key(worker_id: str, namespace: str | None = None) -> str:
    """Worker 心跳 key，与权威 Lease 共享 Redis Cluster slot。"""
    return f"{{{redis_namespace(namespace)}}}:heartbeat:{worker_id}"


def worker_group(namespace: str | None = None) -> str:
    """任务消费组名称。"""
    return f"{redis_namespace(namespace)}-workers"


def control_group(namespace: str | None = None) -> str:
    """控制消费组名称。"""
    return f"{redis_namespace(namespace)}-control"


def control_global_group(worker_id: str, namespace: str | None = None) -> str:
    """Return the Gateway-owned per-Worker group for global broadcasts."""
    normalized_worker_id = (worker_id or "").strip()
    if not normalized_worker_id:
        raise ValueError("worker_id 不能为空")
    return f"{control_group(namespace)}:{normalized_worker_id}"


def direct_register_proof_key(worker_id: str, namespace: str | None = None) -> str:
    """Worker Direct 注册证明 key。"""
    return f"{redis_namespace(namespace)}:direct:register:{worker_id}"


def install_key_redis_digest(value: str) -> str:
    """固定长度哈希，防止安装 Key 相关 Redis key 泄漏外部输入。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def worker_install_key_fail_counter_key(
    key: str,
    source: str,
    namespace: str | None = None,
) -> str:
    """安装 Key 失败计数器 key。"""
    return (
        f"{redis_namespace(namespace)}:worker:install-key:fail:"
        f"{install_key_redis_digest(key)}:{install_key_redis_digest(source)}"
    )


def worker_install_key_block_key(
    key: str,
    source: str,
    namespace: str | None = None,
) -> str:
    """安装 Key 来源封禁 key。"""
    return (
        f"{redis_namespace(namespace)}:worker:install-key:block:"
        f"{install_key_redis_digest(key)}:{install_key_redis_digest(source)}"
    )


def worker_install_source_fail_counter_key(source: str, namespace: str | None = None) -> str:
    """安装注册来源失败计数器 key。"""
    return f"{redis_namespace(namespace)}:worker:install-key:source-fail:{install_key_redis_digest(source)}"


def worker_install_source_block_key(source: str, namespace: str | None = None) -> str:
    """安装注册来源封禁 key。"""
    return f"{redis_namespace(namespace)}:worker:install-key:source-block:{install_key_redis_digest(source)}"


def worker_install_key_claim_key(key: str, namespace: str | None = None) -> str:
    """安装 Key 来源声明 key。"""
    return f"{redis_namespace(namespace)}:worker:install-key:claim:{install_key_redis_digest(key)}"


def worker_install_key_nonce_key(
    key: str,
    request_nonce: str,
    namespace: str | None = None,
) -> str:
    """安装 Key 防重放 nonce key。"""
    return (
        f"{redis_namespace(namespace)}:worker:install-key:nonce:"
        f"{install_key_redis_digest(key)}:{install_key_redis_digest(request_nonce)}"
    )


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
    *,
    payload: Mapping[str, Any] | None = None,
    expires_at_ms: int,
) -> dict[str, str]:
    """构建运行时管理控制指令。"""
    return {
        "control_type": "runtime_manage",
        "action": action,
        "request_id": request_id,
        "reply_stream": reply_stream,
        "payload": json.dumps(dict(payload or {}), ensure_ascii=False),
        "expires_at_ms": str(expires_at_ms),
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
                # 兼容旧写入端的空字符串字段：跳过而非报错。
                continue
            try:
                decoded[json_key] = json.loads(raw, parse_constant=_reject_json_constant)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"Redis Stream 字段 {json_key} 不是合法 JSON") from exc

    return decoded


__all__ = [
    "redis_namespace",
    "scheduler_dispatch_fencing_key",
    "task_ready_stream",
    "task_result_stream",
    "log_stream_key",
    "log_ingest_stream_key",
    "log_chunk_stream_key",
    "log_stream_pattern",
    "log_chunk_stream_pattern",
    "control_stream",
    "control_global_stream",
    "control_global_group",
    "control_reply_stream",
    "validate_worker_key_segment",
    "runtime_control_request_id",
    "require_runtime_control_request_id",
    "runtime_control_settlement_key",
    "cancel_tombstone_key",
    "worker_heartbeat_key",
    "worker_group",
    "control_group",
    "direct_register_proof_key",
    "install_key_redis_digest",
    "worker_install_key_fail_counter_key",
    "worker_install_key_block_key",
    "worker_install_source_fail_counter_key",
    "worker_install_source_block_key",
    "worker_install_key_claim_key",
    "worker_install_key_nonce_key",
    "build_cancel_control_payload",
    "build_config_update_control_payload",
    "build_runtime_manage_control_payload",
    "decode_stream_payload",
]
