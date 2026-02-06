"""
公共工具模块

提供统一的工具函数，包括：
- json_parser: JSON 解析
- http_client: HTTP 客户端
- api_optimizer: API 响应优化
- db_optimizer: 数据库查询优化
- memory_optimizer: 内存管理
- worker_request: Worker 请求构建

注意：hash_utils 和 serialization 已迁移至 common 模块
"""

from antcode_core.common.utils.http_client import http_client
from antcode_core.common.utils.json_parser import (
    JSONParser,
    parse_cookies,
    parse_headers,
    parse_json_safely,
)

__all__ = [
    # JSON 解析
    "JSONParser",
    "parse_json_safely",
    "parse_headers",
    "parse_cookies",
    # HTTP 客户端
    "http_client",
]
