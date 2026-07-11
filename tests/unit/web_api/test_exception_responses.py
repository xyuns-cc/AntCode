import json

import pytest
from antcode_web_api.exceptions import http_exception_handler
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_http_exception_handler_returns_structured_validation_errors():
    response = await http_exception_handler(
        request=None,
        exc=HTTPException(
            status_code=422,
            detail=[
                {
                    "loc": ("env_name",),
                    "msg": "Extra inputs are not permitted",
                    "type": "extra_forbidden",
                }
            ],
        ),
    )

    body = json.loads(response.body)

    assert response.status_code == 422
    assert body["message"] == "请求参数验证失败"
    assert body["data"]["errors"] == [{"field": "env_name", "message": "Extra inputs are not permitted"}]
