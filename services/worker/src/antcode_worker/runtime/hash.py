"""
运行时哈希计算

实现确定性哈希计算，用于运行时环境的缓存和复用。

Requirements: 6.3
"""

import hashlib
import json
from typing import Any

from antcode_worker.runtime.spec import RuntimeSpec


def _normalize_value(value: Any) -> Any:
    """
    规范化值，确保序列化的确定性

    - 字典按键排序
    - 列表保持顺序（除非是集合语义）
    - None 转换为 null
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, (int, float, str, bool)):
        return value
    # 其他类型转换为字符串
    return str(value)


def _canonical_json(data: dict[str, Any]) -> str:
    """
    生成规范化的 JSON 字符串

    确保相同的数据总是生成相同的字符串：
    - 键按字母顺序排序
    - 无多余空白
    - 使用 ASCII 编码
    """
    normalized = _normalize_value(data)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_runtime_hash(spec: RuntimeSpec) -> str:
    """
    计算运行时规格的确定性哈希

    只使用确定性字段计算哈希，非确定性字段（env_vars、secrets）不参与。

    Args:
        spec: 运行时规格

    Returns:
        16 字符的十六进制哈希字符串

    Requirements: 6.3
    """
    # 获取确定性字段
    deterministic_fields = spec.get_deterministic_fields()

    # 生成规范化 JSON
    canonical = _canonical_json(deterministic_fields)

    # 计算 SHA256 哈希
    hash_obj = hashlib.sha256(canonical.encode("utf-8"))

    # 返回前 16 个字符（64 位）
    return hash_obj.hexdigest()[:16]


def compute_content_hash(content: str | bytes) -> str:
    """
    计算内容哈希

    用于计算锁文件、requirements 等内容的哈希。

    Args:
        content: 字符串或字节内容

    Returns:
        16 字符的十六进制哈希字符串
    """
    if isinstance(content, str):
        content = content.encode("utf-8")

    hash_obj = hashlib.sha256(content)
    return hash_obj.hexdigest()[:16]


def compute_requirements_hash(requirements: list[str]) -> str:
    """
    计算 requirements 列表的哈希

    对 requirements 进行排序和规范化后计算哈希。

    Args:
        requirements: 依赖列表

    Returns:
        16 字符的十六进制哈希字符串
    """
    # 规范化：去除空白、排序、去重
    normalized = sorted({req.strip() for req in requirements if req.strip()})

    # 生成规范化字符串
    content = "\n".join(normalized)

    return compute_content_hash(content)


def compute_file_hash(file_path: str) -> str:
    """
    计算文件内容哈希

    Args:
        file_path: 文件路径

    Returns:
        16 字符的十六进制哈希字符串

    Raises:
        FileNotFoundError: 文件不存在
        IOError: 读取文件失败
    """
    hash_obj = hashlib.sha256()

    with open(file_path, "rb") as f:
        # 分块读取大文件
        for chunk in iter(lambda: f.read(8192), b""):
            hash_obj.update(chunk)

    return hash_obj.hexdigest()[:16]


def verify_runtime_hash(spec: RuntimeSpec, expected_hash: str) -> bool:
    """
    验证运行时规格的哈希是否匹配

    Args:
        spec: 运行时规格
        expected_hash: 期望的哈希值

    Returns:
        是否匹配
    """
    actual_hash = compute_runtime_hash(spec)
    return actual_hash == expected_hash


class RuntimeHasher:
    """
    运行时哈希计算器

    提供缓存和批量计算功能。
    """

    def __init__(self):
        self._cache: dict[int, str] = {}

    def compute(self, spec: RuntimeSpec, use_cache: bool = True) -> str:
        """
        计算运行时哈希

        Args:
            spec: 运行时规格
            use_cache: 是否使用缓存

        Returns:
            哈希字符串
        """
        if use_cache:
            # 使用 spec 的 Python hash 作为缓存键
            cache_key = hash(spec)
            if cache_key in self._cache:
                return self._cache[cache_key]

            result = compute_runtime_hash(spec)
            self._cache[cache_key] = result
            return result

        return compute_runtime_hash(spec)

    def clear_cache(self) -> None:
        """清除缓存"""
        self._cache.clear()

    def cache_size(self) -> int:
        """获取缓存大小"""
        return len(self._cache)


# 全局哈希计算器实例
_hasher = RuntimeHasher()


def get_hasher() -> RuntimeHasher:
    """获取全局哈希计算器"""
    return _hasher
