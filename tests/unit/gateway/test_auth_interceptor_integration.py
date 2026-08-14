"""Gateway 认证拦截器端到端：成功流必须真的进业务，失败流必须真的被拒。

回归的缺陷：这两个用例唯一的实质断言都是 ``result.unary_unary is not None``
—— 成功 handler 与 reject handler 同样满足，于是"配置一个必然失败的
validator"这件事完全没有被验证。拦截器哪天把拒绝路径退化成放行，测试照绿。

现在两条用例都**调用**拿到的 handler：
- 成功流断言业务函数被执行、拿到返回值、且 ``AUTHENTICATED_WORKER_ID``
  已绑定到凭据解析出的主体（而不是客户端声明的 header）。
- 失败流断言 abort(UNAUTHENTICATED) 被触发、业务函数一次都没被调到。

写法对齐同目录 ``test_auth_rate_limit_ordering.py``：只替换 context 与凭据
校验边界，拦截器本身是真实实现。
"""

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_gateway.auth import AuthInterceptor, get_authenticated_worker_id

API_KEY = "test-api-key"
WORKER_ID = "worker-001"
METHOD = "/antcode.v1.GatewayService/SendHeartbeat"
BUSINESS_RESULT = "business-ran"


class _AbortCalled(RuntimeError):
    """替代 grpc 在 ``context.abort`` 里抛出的控制流异常。"""

    def __init__(self, code, details) -> None:
        super().__init__(details)
        self.code = code


def _context() -> MagicMock:
    context = MagicMock()
    context.peer.return_value = "ipv4:203.0.113.9:4444"
    context.invocation_metadata.return_value = []
    # 开发环境无 mTLS：auth_context 为空 dict，证书绑定校验放行。
    context.auth_context.return_value = {}

    async def abort(code, details):
        raise _AbortCalled(code, details)

    context.abort = AsyncMock(side_effect=abort)
    return context


def _details(metadata: list[tuple[str, str]]) -> MagicMock:
    details = MagicMock()
    details.method = METHOD
    details.invocation_metadata = metadata
    return details


def _continuation(calls: list) -> AsyncMock:
    """真实的 unary-unary handler，记录业务函数是否被执行到。"""

    async def business(request, _context):
        # 拦截器必须在进入业务前把已认证主体写进 contextvar。
        calls.append((request, get_authenticated_worker_id()))
        return BUSINESS_RESULT

    return AsyncMock(return_value=grpc.unary_unary_rpc_method_handler(business))


@pytest.mark.asyncio
async def test_full_auth_flow_success_reaches_the_business_handler():
    interceptor = AuthInterceptor(
        enabled=True,
        api_key_validator=lambda key, worker_id: key == API_KEY and worker_id == WORKER_ID,
    )
    business_calls: list = []
    continuation = _continuation(business_calls)
    details = _details([("x-api-key", API_KEY), ("x-worker-id", WORKER_ID)])
    context = _context()

    handler = await interceptor.intercept_service(continuation, details)
    response = await handler.unary_unary("request-sentinel", context)

    continuation.assert_called_once_with(details)
    assert response == BUSINESS_RESULT
    assert business_calls == [("request-sentinel", WORKER_ID)]
    context.abort.assert_not_awaited()
    # 业务执行完毕后主体必须还原，不能泄漏到下一个 RPC。
    assert get_authenticated_worker_id() is None


@pytest.mark.asyncio
async def test_full_auth_flow_failure_aborts_and_bypasses_the_business_handler():
    interceptor = AuthInterceptor(
        enabled=True,
        api_key_validator=lambda _key, _worker_id: False,
    )
    business_calls: list = []
    continuation = _continuation(business_calls)
    details = _details([("x-api-key", "invalid_key"), ("x-worker-id", WORKER_ID)])
    context = _context()

    handler = await interceptor.intercept_service(continuation, details)
    with pytest.raises(_AbortCalled) as raised:
        await handler.unary_unary("request-sentinel", context)

    # 拒绝 handler 仍须由 continuation 产出，才能保持 cardinality 与序列化器一致。
    continuation.assert_called_once_with(details)
    assert raised.value.code is grpc.StatusCode.UNAUTHENTICATED
    assert business_calls == []


@pytest.mark.asyncio
async def test_api_key_without_worker_id_header_is_rejected():
    """P0-a1：只给 key 不给 X-Worker-ID 时不得凭 key 前缀伪造主体。"""
    interceptor = AuthInterceptor(
        enabled=True,
        api_key_validator=lambda _key, _worker_id: True,
    )
    business_calls: list = []
    handler = await interceptor.intercept_service(_continuation(business_calls), _details([("x-api-key", API_KEY)]))

    with pytest.raises(_AbortCalled) as raised:
        await handler.unary_unary("request-sentinel", _context())

    assert raised.value.code is grpc.StatusCode.UNAUTHENTICATED
    assert business_calls == []
