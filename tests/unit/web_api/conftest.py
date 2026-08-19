"""web_api 单元测试共享 fixture。"""

import pytest
import pytest_asyncio
from fastapi import Request
from tortoise import Tortoise


@pytest.fixture
def http_request() -> Request:
    """带 client 地址的最小 ASGI 请求；审计写入要从它取 IP。"""
    return Request({"type": "http", "client": ("127.0.0.1", 1234)})


@pytest_asyncio.fixture
async def audit_table():
    """真表 audit_logs。审计用例断言的是"库里真的多出一行"，所以不能 mock 掉写入。"""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()
