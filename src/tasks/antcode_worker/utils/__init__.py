"""
Worker 工具模块

提供独立于 Master 的工具函数，包括：
- hash_utils: 哈希计算
- serialization: JSON/MessagePack 序列化
- exceptions: 自定义异常
"""

from .hash_utils import (
    calculate_file_hash,
    calculate_content_hash,
    verify_file_hash,
    create_hash_calculator,
)
from .serialization import (
    Serializer,
    to_json,
    from_json,
    to_msgpack,
    from_msgpack,
    json_dump_file,
    json_load_file,
)
from .exceptions import (
    SerializationError,
    SecurityError,
)

__all__ = [
    # hash_utils
    "calculate_file_hash",
    "calculate_content_hash",
    "verify_file_hash",
    "create_hash_calculator",
    # serialization
    "Serializer",
    "to_json",
    "from_json",
    "to_msgpack",
    "from_msgpack",
    "json_dump_file",
    "json_load_file",
    # exceptions
    "SerializationError",
    "SecurityError",
]
