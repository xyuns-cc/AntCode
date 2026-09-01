"""控制面对 Worker 已签名请求的拒绝必须带**结构化归因**。

回归的是这条真机故障链：清空控制面 PG 后保留 Worker 侧旧凭据，三台 Worker 永久
崩溃循环并报 ``RuntimeError: 签名验证失败``。真因是库里已经没有这个 worker_id，
而"签名验证失败"这句话把人引向 HMAC 密钥与时钟。判据回答的是"这份凭据长得对
不对"，调用方要问的是"这份凭据还认不认"。

一正一反：身份不存在与签名不符走的是同一个 401，两者必须给出**不同的**码，
否则"能区分"这件事没有被证明。
"""

import pytest
from antcode_core.common.security import WORKER_HTTP_SIGNATURE_HEADER
from antcode_core.common.security.worker_auth import WorkerAuthRejected, WorkerAuthVerifier
from antcode_core.common.security.worker_auth_reasons import WorkerAuthReason
from fastapi import HTTPException

from tests.unit.core.worker_auth_support import (
    HTTP_UNAUTHORIZED,
    WORKER_ID,
    RateLimiterSpy,
    build_request,
    load_shared_secret,
    signature_headers,
)

_WORKER_ID_HEADER = {"X-Worker-ID": WORKER_ID}
_UNSUPPORTED_VERSION = "99"


async def _missing_secret(_worker_id: str) -> str | None:
    """控制面库被重建/回滚后的真实状态：worker_id 查无此人，取不到 HMAC 材料。"""
    return None


async def _claim_nonce(_worker_id: str, _nonce: str) -> bool:
    return True


async def _reject(verifier: WorkerAuthVerifier, headers: dict[str, str]) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify_request(build_request(headers, {}))
    return exc_info.value


@pytest.mark.asyncio
async def test_unknown_worker_identity_is_not_reported_as_signature_failure() -> None:
    verifier = WorkerAuthVerifier(_missing_secret, _claim_nonce, RateLimiterSpy())

    rejection = await _reject(verifier, {**_WORKER_ID_HEADER, **signature_headers({})})

    assert rejection.status_code == HTTP_UNAUTHORIZED
    assert rejection.error_code == WorkerAuthReason.IDENTITY_UNKNOWN.value
    # 旧行为就是这一句；它一旦回来，运维又会去查 HMAC 和时钟。
    assert rejection.detail != "签名验证失败"
    assert "控制面不认识该 Worker 身份" in rejection.detail
    assert "重新注册" in rejection.detail


@pytest.mark.asyncio
async def test_wrong_signature_for_known_worker_keeps_signature_invalid_code() -> None:
    verifier = WorkerAuthVerifier(load_shared_secret, _claim_nonce, RateLimiterSpy())
    headers = {**_WORKER_ID_HEADER, **signature_headers({}), "X-Signature": "invalid"}

    rejection = await _reject(verifier, headers)

    assert rejection.status_code == HTTP_UNAUTHORIZED
    assert rejection.error_code == WorkerAuthReason.SIGNATURE_INVALID.value
    # 反面：真的签名不符不得被说成身份丢失，否则运维会去清一份完全正常的凭据。
    assert rejection.error_code != WorkerAuthReason.IDENTITY_UNKNOWN.value
    assert "控制面不认识该 Worker 身份" not in rejection.detail


@pytest.mark.asyncio
async def test_unsupported_signature_version_is_rejected_with_its_own_code() -> None:
    """签名协议一升版，全部存量 Worker 走的就是这条路径。

    它以前在 ``_signature_headers`` 抛裸 ``HTTPException``，回包 ``data`` 为 null，
    ``verify_signature_async`` 里那个返回 ``SIGNATURE_VERSION_UNSUPPORTED`` 的分支
    因为跑在它后面而结构上永不可达。Worker 侧只认 ``data.error_code``，拿不到码就
    落回 ``RuntimeError`` + 重启环——正是本模块存在的理由。
    """
    limiter = RateLimiterSpy()
    verifier = WorkerAuthVerifier(load_shared_secret, _claim_nonce, limiter)
    headers = {
        **_WORKER_ID_HEADER,
        **signature_headers({}),
        WORKER_HTTP_SIGNATURE_HEADER: _UNSUPPORTED_VERSION,
    }

    rejection = await _reject(verifier, headers)

    assert isinstance(rejection, WorkerAuthRejected)
    assert rejection.status_code == HTTP_UNAUTHORIZED
    assert rejection.error_code == WorkerAuthReason.SIGNATURE_VERSION_UNSUPPORTED.value
    # 反面：版本不支持不得被折叠进"签名不符"，那会把人引向 HMAC 密钥和时钟。
    assert rejection.error_code != WorkerAuthReason.SIGNATURE_INVALID.value
    # 版本不对连一次 Redis 往返都不值得，判定必须仍排在限流之前。
    assert limiter.calls == []


@pytest.mark.asyncio
async def test_identity_unknown_never_consumes_a_nonce() -> None:
    """身份不存在时不该继续走验签与 nonce 消费——那是拿不存在的密钥做无用功。"""
    claimed: list[tuple[str, str]] = []

    async def claim(worker_id: str, nonce: str) -> bool:
        claimed.append((worker_id, nonce))
        return True

    verifier = WorkerAuthVerifier(_missing_secret, claim, RateLimiterSpy())

    await _reject(verifier, {**_WORKER_ID_HEADER, **signature_headers({})})

    assert claimed == []
