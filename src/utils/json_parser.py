"""
JSON解析工具模块
提供安全的JSON解析功能，支持多种输入格式
"""

import ast
import re
from typing import Any, Dict, List, Optional, TypeVar

import ujson
from loguru import logger

T = TypeVar('T')


class JSONParser:
    """JSON解析器类"""

    @staticmethod
    def parse_safely(data, field_name="data") -> Optional[Dict[str, Any]]:
        """
        安全解析JSON字符串或字典
        
        Args:
            data: 待解析的数据，可以是字符串、字典或None
            field_name: 字段名称，用于日志记录
            
        Returns:
            解析后的字典对象，失败时返回None
        """
        if data is None:
            return None

        # 如果已经是字典类型，直接返回
        if isinstance(data, dict):
            return data

        # 如果不是字符串，记录警告并返回None
        if not isinstance(data, str):
            logger.warning(f"无法解析{field_name}: 不支持的数据类型 {type(data)}")
            return None

        # 去除首尾空白字符
        data = data.strip()
        if not data:
            return None

        # 尝试多种解析方式
        parsers = [
            ("ujson.loads", ujson.loads),
            ("ast.literal_eval", ast.literal_eval),
            ("单引号修复后解析", JSONParser._parse_with_quote_fix)
        ]

        for parser_name, parser_func in parsers:
            try:
                result = parser_func(data)
                if isinstance(result, dict):
                    return result
                else:
                    logger.warning(f"{field_name}解析结果不是字典类型: {type(result)}")
            except Exception as e:
                logger.debug(f"使用{parser_name}解析{field_name}失败: {e}")
                continue

        # 所有解析方式都失败
        logger.warning(f"无法解析{field_name}: {data[:100]}...")
        return None

    @staticmethod
    def parse_list(data, field_name="data") -> Optional[List[Any]]:
        """
        安全解析JSON数组
        
        Args:
            data: 待解析的数据，可以是字符串、列表或None
            field_name: 字段名称，用于日志记录
            
        Returns:
            解析后的列表对象，失败时返回None
        """
        if data is None:
            return None

        # 如果已经是列表类型，直接返回
        if isinstance(data, list):
            return data

        # 如果不是字符串，记录警告并返回None
        if not isinstance(data, str):
            logger.warning(f"无法解析{field_name}: 不支持的数据类型 {type(data)}")
            return None

        # 去除首尾空白字符
        data = data.strip()
        if not data:
            return None

        # 尝试多种解析方式
        parsers = [
            ("ujson.loads", ujson.loads),
            ("ast.literal_eval", ast.literal_eval),
            ("单引号修复后解析", JSONParser._parse_list_with_quote_fix)
        ]

        for parser_name, parser_func in parsers:
            try:
                result = parser_func(data)
                if isinstance(result, list):
                    return result
                else:
                    logger.debug(f"{field_name}解析结果不是列表类型: {type(result)}")
            except Exception as e:
                logger.debug(f"使用{parser_name}解析{field_name}失败: {e}")
                continue

        # 所有解析方式都失败
        logger.warning(f"无法解析{field_name}为列表: {data[:100]}...")
        return None

    @staticmethod
    def parse_extraction_rules(data, field_name="extraction_rules") -> Optional[List[Dict[str, Any]]]:
        """
        解析提取规则JSON字符串
        
        Args:
            data: 待解析的数据，可以是字符串、列表或None
            field_name: 字段名称，用于日志记录
            
        Returns:
            解析后的提取规则列表，失败时返回None
        """
        if data is None:
            return None

        # 如果已经是列表类型，直接返回
        if isinstance(data, list):
            return data

        # 使用 parse_list 解析
        result = JSONParser.parse_list(data, field_name)
        if result is None:
            return None

        # 验证每个元素都是字典
        for i, item in enumerate(result):
            if not isinstance(item, dict):
                logger.warning(f"{field_name}[{i}]不是字典类型: {type(item)}")
                return None

        return result

    @staticmethod
    def parse_pagination_config(data, field_name="pagination_config") -> Optional[Dict[str, Any]]:
        """
        解析分页配置JSON字符串
        
        Args:
            data: 待解析的数据，可以是字符串、字典或None
            field_name: 字段名称，用于日志记录
            
        Returns:
            解析后的分页配置字典，失败时返回None
        """
        return JSONParser.parse_safely(data, field_name)

    @staticmethod
    def parse_or_default(data, default_value: T, field_name="data") -> T:
        """
        解析JSON，失败时返回默认值
        
        Args:
            data: 待解析的数据
            default_value: 解析失败时返回的默认值
            field_name: 字段名称，用于日志记录
            
        Returns:
            解析后的数据或默认值
        """
        if isinstance(default_value, list):
            result = JSONParser.parse_list(data, field_name)
        else:
            result = JSONParser.parse_safely(data, field_name)

        return result if result is not None else default_value

    @staticmethod
    def _parse_with_quote_fix(data: str) -> Dict[str, Any]:
        """
        修复单引号格式后解析JSON字典
        
        Args:
            data: JSON字符串
            
        Returns:
            解析后的字典
            
        Raises:
            ValueError: 解析失败时抛出异常
        """
        # 修复单引号为双引号
        # 匹配格式: 'key': 'value' 或 'key' : 'value'
        fixed_json = re.sub(r"'([^']*)'(\s*:\s*)", r'"\1"\2', data)
        fixed_json = re.sub(r"(\s*:\s*)'([^']*)'", r'\1"\2"', fixed_json)

        return ujson.loads(fixed_json)

    @staticmethod
    def _parse_list_with_quote_fix(data: str) -> List[Any]:
        """
        修复单引号格式后解析JSON数组
        
        Args:
            data: JSON字符串
            
        Returns:
            解析后的列表
            
        Raises:
            ValueError: 解析失败时抛出异常
        """
        # 修复单引号为双引号
        fixed_json = re.sub(r"'([^']*)'(\s*:\s*)", r'"\1"\2', data)
        fixed_json = re.sub(r"(\s*:\s*)'([^']*)'", r'\1"\2"', fixed_json)

        return ujson.loads(fixed_json)

    @staticmethod
    def parse_headers(headers):
        """
        解析请求头数据
        
        Args:
            headers: 请求头数据
            
        Returns:
            解析后的请求头字典
        """
        result = JSONParser.parse_safely(headers, "headers")
        if result is None:
            return None

        # 确保所有值都是字符串类型
        return {str(k): str(v) for k, v in result.items()}

    @staticmethod
    def parse_cookies(cookies):
        """
        解析Cookie数据
        
        Args:
            cookies: Cookie数据
            
        Returns:
            解析后的Cookie字典
        """
        result = JSONParser.parse_safely(cookies, "cookies")
        if result is None:
            return None

        # 确保所有值都是字符串类型
        return {str(k): str(v) for k, v in result.items()}

    @staticmethod
    def parse_config(config, config_name = "config"):
        """
        解析通用配置数据
        
        Args:
            config: 配置数据
            config_name: 配置名称，用于日志记录
            
        Returns:
            解析后的配置字典
        """
        return JSONParser.parse_safely(config, config_name)


# 提供便捷的模块级函数
def parse_json_safely(data, field_name = "data"):
    """便捷的JSON解析函数"""
    return JSONParser.parse_safely(data, field_name)


def parse_headers(headers):
    """便捷的请求头解析函数"""
    return JSONParser.parse_headers(headers)


def parse_cookies(cookies):
    """便捷的Cookie解析函数"""
    return JSONParser.parse_cookies(cookies)
