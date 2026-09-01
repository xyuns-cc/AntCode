"""运行时环境的确定性哈希，用于缓存与复用。全部取 SHA256 前 16 个十六进制字符。"""

import hashlib
import json
from typing import Any

from antcode_worker.runtime.spec import RuntimeSpec


def _normalize_value(value: Any) -> Any:
    """规范化取值，保证序列化确定：字典按键排序，列表保持原序，其余类型转字符串。"""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _canonical_json(data: dict[str, Any]) -> str:
    """相同数据恒生成相同字符串（排序键 + 无空白 + ASCII），哈希才有可比性。"""
    normalized = _normalize_value(data)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_runtime_hash(spec: RuntimeSpec) -> str:
    """只取确定性字段；``env_vars`` / ``secrets`` 不参与，否则同一环境无法命中缓存。"""
    deterministic_fields = spec.get_deterministic_fields()
    canonical = _canonical_json(deterministic_fields)
    hash_obj = hashlib.sha256(canonical.encode("utf-8"))
    return hash_obj.hexdigest()[:16]


def compute_content_hash(content: str | bytes) -> str:
    """锁文件、requirements 等内容的哈希。"""
    if isinstance(content, str):
        content = content.encode("utf-8")

    hash_obj = hashlib.sha256(content)
    return hash_obj.hexdigest()[:16]


def compute_requirements_hash(requirements: list[str]) -> str:
    """去空白、去重、排序后再哈希，使等价依赖集合得到同一个值。"""
    normalized = sorted({req.strip() for req in requirements if req.strip()})
    content = "\n".join(normalized)
    return compute_content_hash(content)


def compute_file_hash(file_path: str) -> str:
    hash_obj = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_obj.update(chunk)

    return hash_obj.hexdigest()[:16]


def verify_runtime_hash(spec: RuntimeSpec, expected_hash: str) -> bool:
    actual_hash = compute_runtime_hash(spec)
    return actual_hash == expected_hash


class RuntimeHasher:
    """带缓存的运行时哈希计算器。"""

    def __init__(self):
        self._cache: dict[int, str] = {}

    def compute(self, spec: RuntimeSpec, use_cache: bool = True) -> str:
        if use_cache:
            cache_key = hash(spec)
            if cache_key in self._cache:
                return self._cache[cache_key]

            result = compute_runtime_hash(spec)
            self._cache[cache_key] = result
            return result

        return compute_runtime_hash(spec)

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_size(self) -> int:
        return len(self._cache)


_hasher = RuntimeHasher()


def get_hasher() -> RuntimeHasher:
    return _hasher
