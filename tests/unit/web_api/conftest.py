"""web_api 单元测试共享 fixture。"""

import pytest
from fastapi import Request


@pytest.fixture
def http_request() -> Request:
    """带 client 地址的最小 ASGI 请求；审计写入要从它取 IP。"""
    return Request({"type": "http", "client": ("127.0.0.1", 1234)})
