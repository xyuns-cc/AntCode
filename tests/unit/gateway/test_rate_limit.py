from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest
from antcode_gateway import rate_limit as rl
from antcode_gateway.rate_limit import RateLimiter, RateLimitInterceptor, TokenBucketLimiter
from antcode_gateway.redis_token_bucket import RedisTokenBucketResult


def test_token_bucket_denies_when_empty():
    limiter = TokenBucketLimiter(rate=0.0, capacity=1)
    first = limiter.allow("worker-1")
    second = limiter.allow("worker-1")
    assert first.allowed is True
    assert second.allowed is False


def test_rate_limiter_blocks_when_global_empty():
    limiter = RateLimiter(global_rate=0.0, global_capacity=0, per_worker_rate=1.0, per_worker_capacity=1)
    result = limiter.allow("worker-1")
    assert result.allowed is False


# ---------------------------------------------------------------------------
# RateLimitInterceptor: key 派生必须来自不可伪造的来源(真实对端 / 认证主体) +
# 全局上限兜底。
# ---------------------------------------------------------------------------


class _AbortCalled(RuntimeError):
    pass


def _context(peer: str = "ipv4:203.0.113.7:5555", metadata=()):
    ctx = MagicMock()
    ctx.peer.return_value = peer
    ctx.invocation_metadata.return_value = list(metadata)
    ctx.set_trailing_metadata = MagicMock()
    ctx.abort = AsyncMock(side_effect=_AbortCalled)
    return ctx


class _StubLimiter:
    def __init__(self, results):
        self._results = list(results)
        self.calls: list[str] = []

    async def allow(self, identifier: str):
        self.calls.append(identifier)
        return self._results.pop(0)


_ALLOW = RedisTokenBucketResult(allowed=True, remaining=5, reset_at=0.0, retry_after=0.0)
_DENY = RedisTokenBucketResult(allowed=False, remaining=0, reset_at=1.0, retry_after=0.5)


def test_spoofed_xff_from_untrusted_peer_does_not_mint_new_bucket(monkeypatch):
    # 没有受信代理配置时,伪造的 X-Forwarded-For 必须被忽略,key 恒等于真实对端 IP。
    monkeypatch.delenv("ANTCODE_TRUSTED_PROXIES", raising=False)
    interceptor = RateLimitInterceptor()
    ctx1 = _context(metadata=[("x-forwarded-for", "1.2.3.4")])
    ctx2 = _context(metadata=[("x-forwarded-for", "9.9.9.9"), ("x-worker-id", "spoofed")])
    key1 = interceptor._resolve_key(dict(ctx1.invocation_metadata()), ctx1)
    key2 = interceptor._resolve_key(dict(ctx2.invocation_metadata()), ctx2)
    assert key1 == key2 == "ip:203.0.113.7"


def test_trusted_proxy_honors_forwarded_for(monkeypatch):
    monkeypatch.setenv("ANTCODE_TRUSTED_PROXIES", "203.0.113.7")
    interceptor = RateLimitInterceptor()
    ctx = _context(metadata=[("x-forwarded-for", "1.2.3.4")])
    key = interceptor._resolve_key(dict(ctx.invocation_metadata()), ctx)
    assert key == "ip:1.2.3.4"


def test_authenticated_identity_is_keyed_over_header(monkeypatch):
    # 已认证方法:key 必须来自服务端认证主体,而不是客户端声明的 x-worker-id。
    monkeypatch.setattr(rl, "get_authenticated_worker_id", lambda: "worker-auth")
    interceptor = RateLimitInterceptor()
    ctx = _context(metadata=[("x-worker-id", "spoofed"), ("x-forwarded-for", "1.2.3.4")])
    key = interceptor._resolve_key(dict(ctx.invocation_metadata()), ctx)
    assert key == "worker:worker-auth"


def test_ipv6_peer_is_parsed():
    interceptor = RateLimitInterceptor()
    assert interceptor._peer_ip("ipv6:[::1]:5678") == "::1"
    assert interceptor._peer_ip("ipv4:1.2.3.4:9") == "1.2.3.4"
    assert interceptor._peer_ip("unix:/tmp/sock") == ""


@pytest.mark.asyncio
async def test_global_ceiling_triggers_before_per_key(monkeypatch):
    monkeypatch.delenv("ANTCODE_TRUSTED_PROXIES", raising=False)
    interceptor = RateLimitInterceptor()
    global_stub = _StubLimiter([_DENY])
    key_stub = _StubLimiter([_ALLOW])
    interceptor._global_limiter = global_stub
    interceptor._limiter = key_stub

    ctx = _context()
    with pytest.raises(_AbortCalled):
        await interceptor._check(ctx, "/antcode.v1.ControlService/Register")

    ctx.abort.assert_awaited_once()
    assert ctx.abort.await_args.args[0] == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert global_stub.calls == ["global"]
    # 全局桶已满时短路,不消耗 per-key 令牌。
    assert key_stub.calls == []


@pytest.mark.asyncio
async def test_per_key_denied_aborts_with_trailing_metadata(monkeypatch):
    monkeypatch.delenv("ANTCODE_TRUSTED_PROXIES", raising=False)
    interceptor = RateLimitInterceptor()
    interceptor._global_limiter = _StubLimiter([_ALLOW])
    interceptor._limiter = _StubLimiter([_DENY])

    ctx = _context()
    with pytest.raises(_AbortCalled):
        await interceptor._check(ctx, "/antcode.v1.DataService/StreamSpiderData")
    ctx.set_trailing_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_allowed_request_invokes_wrapped_handler(monkeypatch):
    monkeypatch.delenv("ANTCODE_TRUSTED_PROXIES", raising=False)
    interceptor = RateLimitInterceptor()
    interceptor._global_limiter = _StubLimiter([_ALLOW])
    interceptor._limiter = _StubLimiter([_ALLOW])

    async def handler(_request, _context):
        return "ok"

    original = grpc.unary_unary_rpc_method_handler(handler)
    wrapped = interceptor._wrap_handler(original, "/antcode.v1.ControlService/Register")
    assert await wrapped.unary_unary(None, _context()) == "ok"
