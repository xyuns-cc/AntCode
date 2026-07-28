"""内容确定性 ``LogBatch.batch_id`` 的唯一实现（Worker 生产 / 服务端复核）。

复审 P1-GW-05: ``batch_id`` 是日志幂等键（event_id = ``batch_id:index``），
契约必须闭环：

- **格式恒定**：sha256 hexdigest，恰好 64 个小写十六进制字符。event_id
  因此有硬上界（64 + 1 + 批内下标位数 ≪ 128 列宽），不可能溢出
  ``task_logs.event_id`` 而制造永久 PEL。
- **服务端复核**：Gateway/Master 用同一函数对 entries 重算哈希并与声明
  值比对。同 ``batch_id`` 携带不同内容的批次会被显式拒绝，而不是被
  ``ON CONFLICT DO NOTHING`` 静默吞掉后发布旧行。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_ENTRY_LENGTH_PREFIX_BYTES = 4
# sha256 hexdigest 恒为 64 字符；构批阶段用同长度占位符参与 ByteSize 预算。
BATCH_ID_HEX_LENGTH = 64
BATCH_ID_PLACEHOLDER = "0" * BATCH_ID_HEX_LENGTH
_BATCH_ID_PATTERN = re.compile(r"[0-9a-f]{64}")


def deterministic_batch_id(worker_id: str, entries: Any) -> str:
    """内容哈希 batch_id：同批内容 → 同 ID（跨重试稳定）。

    对每条 entry 的 deterministic protobuf bytes 做长度前缀哈希，避免
    相邻 entry 拼接歧义。
    """
    hasher = hashlib.sha256()
    hasher.update(worker_id.encode("utf-8"))
    for entry in entries:
        piece = entry.SerializeToString(deterministic=True)
        hasher.update(len(piece).to_bytes(_ENTRY_LENGTH_PREFIX_BYTES, "big"))
        hasher.update(piece)
    return hasher.hexdigest()


def is_canonical_batch_id(value: str) -> bool:
    """batch_id 是否为规范 sha256 hex（64 个小写十六进制字符）。"""
    return bool(_BATCH_ID_PATTERN.fullmatch(value))


def verify_batch_id(worker_id: str, entries: Any, declared: str) -> bool:
    """服务端复核：声明的 batch_id 必须等于对 entries 重算的内容哈希。"""
    if not is_canonical_batch_id(declared):
        return False
    return deterministic_batch_id(worker_id, entries) == declared


__all__ = [
    "BATCH_ID_HEX_LENGTH",
    "BATCH_ID_PLACEHOLDER",
    "deterministic_batch_id",
    "is_canonical_batch_id",
    "verify_batch_id",
]
