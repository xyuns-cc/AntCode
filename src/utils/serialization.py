"""
高性能序列化工具模块
提供统一的 JSON 和 MessagePack 序列化/反序列化功能
使用 ujson 替代标准 json 库以提升性能
"""

from datetime import datetime
from typing import Any, Union

import msgpack
import ujson
from loguru import logger

from src.core.exceptions import SerializationError


def _default_json_serializer(obj: Any) -> Any:
    """
    默认的 JSON 序列化处理器，处理特殊类型
    
    Args:
        obj: 待处理的对象
        
    Returns:
        可序列化的对象
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


class Serializer:
    """高性能序列化器类"""

    @staticmethod
    def to_json(
        obj: Any, 
        ensure_ascii: bool = False, 
        sort_keys: bool = False,
        indent: int = 0,
        default: Any = None
    ) -> str:
        """
        使用 ujson 将对象序列化为 JSON 字符串
        
        Args:
            obj: 待序列化的对象
            ensure_ascii: 是否确保 ASCII 编码，默认 False 以支持中文
            sort_keys: 是否对键排序
            indent: 缩进空格数，0 表示紧凑格式
            default: 自定义序列化处理器，用于处理不可直接序列化的类型
                     如果为 None，则使用默认处理器
            
        Returns:
            JSON 字符串
            
        Raises:
            SerializationError: 序列化失败时抛出
        """
        try:
            serializer = default if default is not None else _default_json_serializer
            return ujson.dumps(
                obj, 
                ensure_ascii=ensure_ascii, 
                sort_keys=sort_keys,
                indent=indent if indent > 0 else 0,
                default=serializer
            )
        except (TypeError, ValueError, OverflowError) as e:
            obj_type = type(obj).__name__
            logger.error(f"JSON 序列化失败: 对象类型 {obj_type}, 错误: {e}")
            raise SerializationError(f"无法序列化类型 {obj_type}: {e}") from e

    @staticmethod
    def from_json(data: str) -> Any:
        """
        使用 ujson 将 JSON 字符串反序列化为对象
        
        Args:
            data: JSON 字符串
            
        Returns:
            反序列化后的对象
            
        Raises:
            SerializationError: 反序列化失败时抛出
        """
        try:
            return ujson.loads(data)
        except (ValueError, TypeError) as e:
            logger.error(f"JSON 反序列化失败: {e}")
            raise SerializationError(f"无法反序列化 JSON: {e}") from e

    @staticmethod
    def to_msgpack(obj: Any) -> bytes:
        """
        使用 msgpack 将对象序列化为二进制数据
        适用于大数据量场景，比 JSON 更快更紧凑
        
        Args:
            obj: 待序列化的对象
            
        Returns:
            msgpack 二进制数据
            
        Raises:
            SerializationError: 序列化失败时抛出
        """
        try:
            return msgpack.packb(obj, use_bin_type=True, default=Serializer._msgpack_default)
        except (TypeError, ValueError) as e:
            obj_type = type(obj).__name__
            logger.error(f"MessagePack 序列化失败: 对象类型 {obj_type}, 错误: {e}")
            raise SerializationError(f"无法序列化类型 {obj_type}: {e}") from e

    @staticmethod
    def from_msgpack(data: bytes) -> Any:
        """
        使用 msgpack 将二进制数据反序列化为对象
        
        Args:
            data: msgpack 二进制数据
            
        Returns:
            反序列化后的对象
            
        Raises:
            SerializationError: 反序列化失败时抛出
        """
        try:
            return msgpack.unpackb(data, raw=False)
        except (ValueError, TypeError, msgpack.UnpackException) as e:
            logger.error(f"MessagePack 反序列化失败: {e}")
            raise SerializationError(f"无法反序列化 MessagePack: {e}") from e

    @staticmethod
    def _msgpack_default(obj: Any) -> Any:
        """
        msgpack 默认序列化处理器，处理特殊类型
        
        Args:
            obj: 待处理的对象
            
        Returns:
            可序列化的对象
            
        Raises:
            TypeError: 不支持的类型
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        raise TypeError(f"不支持的类型: {type(obj).__name__}")


# 便捷的模块级函数
def to_json(obj: Any, ensure_ascii: bool = False, sort_keys: bool = False, indent: int = 0, default: Any = None) -> str:
    """便捷的 JSON 序列化函数"""
    return Serializer.to_json(obj, ensure_ascii, sort_keys, indent, default)


def from_json(data: str) -> Any:
    """便捷的 JSON 反序列化函数"""
    return Serializer.from_json(data)


def to_msgpack(obj: Any) -> bytes:
    """便捷的 MessagePack 序列化函数"""
    return Serializer.to_msgpack(obj)


def from_msgpack(data: bytes) -> Any:
    """便捷的 MessagePack 反序列化函数"""
    return Serializer.from_msgpack(data)


def json_dump_file(obj: Any, file_path: str, ensure_ascii: bool = False, indent: int = 2) -> None:
    """
    将对象序列化为 JSON 并写入文件
    
    Args:
        obj: 待序列化的对象
        file_path: 文件路径
        ensure_ascii: 是否确保 ASCII 编码
        indent: 缩进空格数
    """
    content = Serializer.to_json(obj, ensure_ascii=ensure_ascii, indent=indent)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


def json_load_file(file_path: str) -> Any:
    """
    从文件读取 JSON 并反序列化
    
    Args:
        file_path: 文件路径
        
    Returns:
        反序列化后的对象
    """
    with open(file_path, "r", encoding="utf-8") as f:
        return Serializer.from_json(f.read())


def json_dumps_compact(obj: Any, sort_keys: bool = False) -> str:
    """
    紧凑格式的 JSON 序列化（用于签名等场景）
    
    Args:
        obj: 待序列化的对象
        sort_keys: 是否对键排序
        
    Returns:
        紧凑格式的 JSON 字符串
    """
    return Serializer.to_json(obj, sort_keys=sort_keys, indent=0)
