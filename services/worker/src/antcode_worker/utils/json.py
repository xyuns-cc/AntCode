"""
JSON 工具

Requirements: 13.3
"""

from datetime import datetime
from enum import Enum
from typing import Any

try:
    import ujson as json
except ImportError:
    import json


def dumps(obj: Any, **kwargs) -> str:
    """安全的 JSON 序列化"""
    return json.dumps(obj, default=_default_encoder, ensure_ascii=False, **kwargs)


def loads(s: str) -> Any:
    """JSON 反序列化"""
    return json.loads(s)


def _default_encoder(obj: Any) -> Any:
    """默认编码器"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
