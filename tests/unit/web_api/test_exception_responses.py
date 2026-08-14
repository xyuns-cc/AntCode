import json

import pytest
from antcode_web_api.exceptions import http_exception_handler, validation_exception_handler
from fastapi import HTTPException, status
from fastapi.exceptions import RequestValidationError


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


@pytest.mark.asyncio
async def test_http_exception_handler_preserves_authentication_headers():
    response = await http_exception_handler(
        request=None,
        exc=HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证失败",
            headers={"WWW-Authenticate": "Bearer"},
        ),
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_validation_exception_handler_matches_openapi_422_contract():
    response = await validation_exception_handler(
        request=None,
        exc=RequestValidationError(
            [
                {
                    "type": "int_parsing",
                    "loc": ("query", "page"),
                    "msg": "Input should be a valid integer",
                    "input": "invalid",
                }
            ]
        ),
    )

    body = json.loads(response.body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert body["code"] == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert body["data"]["errors"] == [{"field": "query.page", "message": "Input should be a valid integer"}]
