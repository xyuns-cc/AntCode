"""P0-B10: 认证失败的请求必须同样受限流约束，并且必须留下安全审计。

回归的缺陷：AuthInterceptor 注册在 RateLimitInterceptor 外层，认证失败时它直接返回
自己构造的 abort handler，请求根本不流经限流拦截器包出来的 handler，于是伪造
``x-api-key`` 的调用可零成本无限重放，每次都触发一轮 API Key 查库 + 一条
``audit:security`` XADD —— 这是网关上唯一无需凭据即可触发的放大点。

测试用**真实**的两个拦截器组合，只把 Redis 令牌桶与 DB 查询换成可观测替身。
"""

from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_contracts import control_pb2
from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.auth_credentials import INVALID_API_KEY_ERROR
from antcode_gateway.rate_limit import RateLimitInterceptor
from antcode_gateway.redis_token_bucket import RedisTokenBucketResult
from antcode_gateway.security_audit import (
    AUDIT_SECURITY_STREAM,
    EVENT_API_KEY_INVALID,
    EVENT_REGISTER_REJECTED,
    SecurityAuditor,
)
from antcode_gateway.services import registration_gate
from antcode_gateway.services.control_service import GatewayControlService

PEER = "ipv4:203.0.113.9:4444"
PEER_KEY = "ip:203.0.113.9"
WORKER_ID = "worker-b10"
LEASE_METHOD = "/antcode.v1.ControlService/Lease"

_ALLOW = RedisTokenBucketResult(allowed=True, remaining=5, reset_at=0.0, retry_after=0.0)
_DENY = RedisTokenBucketResult(allowed=False, remaining=0, reset_at=1.0, retry_after=0.5)


class _AbortCalled(RuntimeError):
    """替代 grpc 在 ``context.abort`` 里抛出的控制流异常。"""

    def __init__(self, code, details) -> None:
        super().__init__(details)
        self.code = code


class _RecordingBucket:
    """记录每次扣费所用的限流键，并按预设结果放行/拒绝。"""

    def __init__(self, results: list[RedisTokenBucketResult] | None = None) -> None:
        self._results = list(results or [])
        self.calls: list[str] = []

    async def allow(self, identifier: str) -> RedisTokenBucketResult:
        self.calls.append(identifier)
        return self._results.pop(0) if self._results else _ALLOW


class _RecordingStream:
    """替代 ``StreamClient``，把 audit:security 写入留在内存里。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def xadd(self, stream_key: str, data: dict, *_args, **_kwargs) -> str:
        self.events.append((stream_key, data))
        return "1-1"


def _context() -> MagicMock:
    context = MagicMock()
    context.peer.return_value = PEER
    context.invocation_metadata.return_value = []

    async def abort(code, details):
        raise _AbortCalled(code, details)

    context.abort = AsyncMock(side_effect=abort)
    return context


def _details() -> MagicMock:
    details = MagicMock()
    details.method = LEASE_METHOD
    details.invocation_metadata = [("x-api-key", "forged-key"), ("x-worker-id", WORKER_ID)]
    return details


def _limiter(key_results=None, global_results=None) -> tuple[RateLimitInterceptor, dict]:
    """真实的 RateLimitInterceptor，只把两个 Redis 令牌桶换成记录型替身。"""
    interceptor = RateLimitInterceptor()
    buckets = {"global": _RecordingBucket(global_results), "key": _RecordingBucket(key_results)}
    interceptor._global_limiter, interceptor._limiter = buckets["global"], buckets["key"]
    return interceptor, buckets


def _auth(limiter, audit_stream=None) -> AuthInterceptor:
    """凭据必定校验失败的真实 AuthInterceptor。"""
    return AuthInterceptor(
        enabled=True,
        api_key_validator=lambda _key, _worker_id: False,
        audit_stream=audit_stream,
        rate_limiter=limiter,
    )


def _continuation(calls: list, factory=grpc.unary_unary_rpc_method_handler) -> AsyncMock:
    async def business(request, _context):
        calls.append(request)
        return "business-ran"

    return AsyncMock(return_value=factory(business))


@pytest.fixture(autouse=True)
def _no_trusted_proxies(monkeypatch):
    # 没有受信代理时 _resolve_key 必须落在真实对端 IP 上。
    monkeypatch.delenv("ANTCODE_TRUSTED_PROXIES", raising=False)


@pytest.mark.asyncio
async def test_failed_auth_is_charged_to_the_peer_rate_limit_bucket():
    limiter, buckets = _limiter()
    business_calls: list = []

    handler = await _auth(limiter).intercept_service(_continuation(business_calls), _details())
    with pytest.raises(_AbortCalled) as raised:
        await handler.unary_unary(None, _context())

    assert buckets["global"].calls == ["global"]
    # 未认证 → 限流键退回真实对端 IP，不会污染已认证 worker 的桶。
    assert buckets["key"].calls == [PEER_KEY]
    assert raised.value.code == grpc.StatusCode.UNAUTHENTICATED
    assert business_calls == []


@pytest.mark.asyncio
async def test_failed_auth_beyond_threshold_returns_resource_exhausted():
    limiter, buckets = _limiter(key_results=[_ALLOW, _DENY])
    interceptor = _auth(limiter)
    business_calls: list = []
    continuation = _continuation(business_calls)

    first = await interceptor.intercept_service(continuation, _details())
    with pytest.raises(_AbortCalled) as within_threshold:
        await first.unary_unary(None, _context())

    second = await interceptor.intercept_service(continuation, _details())
    context = _context()
    with pytest.raises(_AbortCalled) as over_threshold:
        await second.unary_unary(None, context)

    assert within_threshold.value.code == grpc.StatusCode.UNAUTHENTICATED
    assert over_threshold.value.code == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert buckets["key"].calls == [PEER_KEY, PEER_KEY]
    context.set_trailing_metadata.assert_called_once()
    assert business_calls == []


@pytest.mark.asyncio
async def test_global_ceiling_stops_unauthenticated_flood_before_the_per_key_bucket():
    limiter, buckets = _limiter(global_results=[_DENY])

    handler = await _auth(limiter).intercept_service(_continuation([]), _details())
    with pytest.raises(_AbortCalled) as raised:
        await handler.unary_unary(None, _context())

    assert raised.value.code == grpc.StatusCode.RESOURCE_EXHAUSTED
    # 全局桶已满时短路，不再消耗 per-key 令牌。
    assert buckets["key"].calls == []


@pytest.mark.parametrize(
    ("key_results", "expected_code", "audited"),
    [
        ([_ALLOW], grpc.StatusCode.UNAUTHENTICATED, True),
        ([_DENY], grpc.StatusCode.RESOURCE_EXHAUSTED, False),
    ],
)
@pytest.mark.asyncio
async def test_audit_is_written_only_after_the_rate_limit_passes(key_results, expected_code, audited):
    """限流必须排在审计写入之前，否则 audit:security 仍会被无凭据请求撑爆。"""
    limiter, _ = _limiter(key_results=key_results)
    audit_stream = _RecordingStream()

    handler = await _auth(limiter, audit_stream).intercept_service(_continuation([]), _details())
    with pytest.raises(_AbortCalled) as raised:
        await handler.unary_unary(None, _context())

    assert raised.value.code == expected_code
    if not audited:
        assert audit_stream.events == []
        return
    stream_key, fields = audit_stream.events[0]
    assert stream_key == AUDIT_SECURITY_STREAM
    assert fields["event_type"] == EVENT_API_KEY_INVALID
    assert fields["worker_id"] == WORKER_ID
    assert fields["reason"] == INVALID_API_KEY_ERROR


@pytest.mark.parametrize(
    ("factory", "attribute"),
    [
        (grpc.unary_unary_rpc_method_handler, "unary_unary"),
        (grpc.unary_stream_rpc_method_handler, "unary_stream"),
        (grpc.stream_unary_rpc_method_handler, "stream_unary"),
        (grpc.stream_stream_rpc_method_handler, "stream_stream"),
    ],
)
@pytest.mark.asyncio
async def test_rate_limited_rejection_preserves_every_cardinality(factory, attribute):
    """限流包装后仍须保持原 RPC 形态，response-streaming 还必须是 async generator。"""
    limiter, buckets = _limiter()

    handler = await _auth(limiter).intercept_service(_continuation([], factory), _details())

    call = getattr(handler, attribute)
    assert call is not None
    with pytest.raises(_AbortCalled) as raised:
        if attribute in ("unary_stream", "stream_stream"):
            async for _ in call(None, _context()):
                pass
        else:
            await call(None, _context())
    assert raised.value.code == grpc.StatusCode.UNAUTHENTICATED
    assert buckets["key"].calls == [PEER_KEY]


@pytest.mark.asyncio
async def test_rejection_without_rate_limiter_still_returns_unauthenticated():
    """RATE_LIMIT_ENABLED=false 时不注入限流器，鉴权判定本身不受影响。"""
    handler = await _auth(None).intercept_service(_continuation([]), _details())
    with pytest.raises(_AbortCalled) as raised:
        await handler.unary_unary(None, _context())

    assert raised.value.code == grpc.StatusCode.UNAUTHENTICATED


# Register 是 auth-exempt 的匿名入口：拦截器不介入，审计只能由 handler 自己发。


def _register_service(audit_stream: _RecordingStream) -> GatewayControlService:
    lease_store = MagicMock()
    lease_store.policy.ttl_ms = 30_000
    lease_store.policy.renew_after_ms = 10_000
    service = GatewayControlService(lease_store=lease_store)
    service.bind_security_auditor(SecurityAuditor(audit_stream))
    return service


@pytest.mark.asyncio
async def test_register_api_key_brute_force_is_audited(monkeypatch):
    audit_stream = _RecordingStream()
    # 只替换 DB 边界，registration_validation_error / 事件分类 / Register 全部真实执行。
    monkeypatch.setattr(registration_gate, "verify_api_key", AsyncMock(return_value=False))
    request = control_pb2.RegisterRequest(worker_id=WORKER_ID, api_key="forged-key")

    response = await _register_service(audit_stream).Register(request, _context())

    assert response.success is False
    assert response.error == INVALID_API_KEY_ERROR
    assert len(audit_stream.events) == 1
    stream_key, fields = audit_stream.events[0]
    assert stream_key == AUDIT_SECURITY_STREAM
    # 与拦截器侧凭据失败共用事件类型，爆破检测规则不必区分入口；peer 取真实对端。
    assert fields["event_type"] == EVENT_API_KEY_INVALID
    assert fields["worker_id"] == WORKER_ID
    assert fields["reason"] == INVALID_API_KEY_ERROR
    assert fields["peer"] == PEER


@pytest.mark.parametrize(("api_key", "reason"), [("", "缺少 API Key"), ("valid-key", "Worker 不存在")])
@pytest.mark.asyncio
async def test_register_non_credential_rejections_use_a_distinct_event_type(monkeypatch, api_key, reason):
    audit_stream = _RecordingStream()
    query = MagicMock()
    query.only.return_value.first = AsyncMock(return_value=None)
    monkeypatch.setattr(registration_gate, "verify_api_key", AsyncMock(return_value=True))
    monkeypatch.setattr(registration_gate.Worker, "filter", MagicMock(return_value=query))

    request = control_pb2.RegisterRequest(worker_id=WORKER_ID, api_key=api_key)
    response = await _register_service(audit_stream).Register(request, _context())

    assert response.success is False
    assert audit_stream.events[0][1]["event_type"] == EVENT_REGISTER_REJECTED
    assert audit_stream.events[0][1]["reason"] == reason


@pytest.mark.asyncio
async def test_successful_register_writes_no_audit_event(monkeypatch):
    audit_stream = _RecordingStream()
    monkeypatch.setattr(
        "antcode_gateway.services.control_service.registration_validation_error",
        AsyncMock(return_value=None),
    )
    request = control_pb2.RegisterRequest(worker_id=WORKER_ID, api_key="valid-key")

    response = await _register_service(audit_stream).Register(request, _context())

    assert response.success is True
    assert audit_stream.events == []
