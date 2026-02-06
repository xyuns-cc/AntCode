"""
高性能序列化工具模块

提供统一的 JSON 和 MessagePack 序列化/反序列化功能。
使用 ujson 替代标准 json 库以提升性能。

Requirements: 8.1
"""

from datetime import datetime

import ujson
from loguru import logger

from antcode_core.common.exceptions import SerializationError

try:
    import msgpack

    HAS_MSGPACK = True
except ImportError:
    msgpack = None  # type: ignore
    HAS_MSGPACK = False


def _default_json_serializer(obj):
    """默认的 JSON 序列化处理器，处理特殊类型"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


class Serializer:
    """高性能序列化器类"""

    @staticmethod
    def to_json(obj, ensure_ascii=False, sort_keys=False, indent=0, default=None):
        """
        将对象序列化为 JSON 字符串

        Args:
            obj: 待序列化的对象
            ensure_ascii: 是否确保 ASCII 编码，默认 False 以支持中文
            sort_keys: 是否对键排序
            indent: 缩进空格数，0 表示紧凑格式
            default: 自定义序列化处理器

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
                default=serializer,
            )
        except (TypeError, ValueError, OverflowError) as e:
            obj_type = type(obj).__name__
            logger.error(f"JSON 序列化失败: 对象类型 {obj_type}, 错误: {e}")
            raise SerializationError(f"无法序列化类型 {obj_type}: {e}") from e

    @staticmethod
    def from_json(data):
        """
        将 JSON 字符串反序列化为对象

        Args:
            data: JSON 字符串或字节

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
    def to_msgpack(obj):
        """
        将对象序列化为 MessagePack 二进制数据

        Args:
            obj: 待序列化的对象

        Returns:
            msgpack 二进制数据

        Raises:
            SerializationError: 序列化失败或 msgpack 未安装时抛出
        """
        if not HAS_MSGPACK:
            raise SerializationError("msgpack 未安装，请运行: pip install msgpack")

        try:
            return msgpack.packb(obj, use_bin_type=True, default=Serializer._msgpack_default)
        except (TypeError, ValueError) as e:
            obj_type = type(obj).__name__
            logger.error(f"MessagePack 序列化失败: 对象类型 {obj_type}, 错误: {e}")
            raise SerializationError(f"无法序列化类型 {obj_type}: {e}") from e

    @staticmethod
    def from_msgpack(data):
        """
        将 MessagePack 二进制数据反序列化为对象

        Args:
            data: msgpack 二进制数据

        Returns:
            反序列化后的对象

        Raises:
            SerializationError: 反序列化失败或 msgpack 未安装时抛出
        """
        if not HAS_MSGPACK:
            raise SerializationError("msgpack 未安装，请运行: pip install msgpack")

        try:
            return msgpack.unpackb(data, raw=False)
        except (ValueError, TypeError, msgpack.UnpackException) as e:
            logger.error(f"MessagePack 反序列化失败: {e}")
            raise SerializationError(f"无法反序列化 MessagePack: {e}") from e

    @staticmethod
    def _msgpack_default(obj):
        """msgpack 默认序列化处理器"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        raise TypeError(f"不支持的类型: {type(obj).__name__}")


# 便捷的模块级函数
def to_json(obj, ensure_ascii=False, sort_keys=False, indent=0, default=None):
    """便捷的 JSON 序列化函数"""
    return Serializer.to_json(obj, ensure_ascii, sort_keys, indent, default)


def from_json(data):
    """便捷的 JSON 反序列化函数"""
    return Serializer.from_json(data)


def to_msgpack(obj):
    """便捷的 MessagePack 序列化函数"""
    return Serializer.to_msgpack(obj)


def from_msgpack(data):
    """便捷的 MessagePack 反序列化函数"""
    return Serializer.from_msgpack(data)


def json_dump_file(obj, file_path, ensure_ascii=False, indent=2):
    """将对象序列化为 JSON 并写入文件"""
    content = Serializer.to_json(obj, ensure_ascii=ensure_ascii, indent=indent)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)


def json_load_file(file_path):
    """从文件读取 JSON 并反序列化"""
    with open(file_path, encoding="utf-8") as f:
        return Serializer.from_json(f.read())


def json_dumps_compact(obj, sort_keys=False):
    """
    紧凑格式的 JSON 序列化（用于签名等场景）

    Args:
        obj: 待序列化的对象
        sort_keys: 是否对键排序

    Returns:
        紧凑格式的 JSON 字符串
    """
    return Serializer.to_json(obj, sort_keys=sort_keys, indent=0)
