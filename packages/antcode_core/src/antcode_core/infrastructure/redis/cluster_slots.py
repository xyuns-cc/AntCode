"""Redis Cluster co-slot hash tag derivation.

多 key Lua 在 Cluster 下要求所有 KEYS 同 slot。对无法改名的既有 key
（如 per-worker task ready stream），派生一个 hash tag 使伴生 key
（marker/DLQ 等）落到与源 key 相同的 slot。
"""

from __future__ import annotations

from functools import lru_cache
from itertools import count

from redis.cluster import key_slot


@lru_cache(maxsize=256)
def co_slot_hash_tag(source_key: str, prefix: str = "cs") -> str:
    """Return a hash tag whose slot equals ``source_key``'s slot."""
    target_slot = key_slot(source_key.encode("utf-8"))
    for candidate in count():
        tag = f"{prefix}{candidate:x}"
        if key_slot(f"{{{tag}}}".encode("ascii")) == target_slot:
            return tag
    raise AssertionError("unreachable")


__all__ = ["co_slot_hash_tag"]
