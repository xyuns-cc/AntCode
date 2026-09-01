"""``_verify_worker_credential_headers`` 的"Worker 不存在"必须带结构化归因。

它守着绝大多数 Worker 端点（含 ``POST /workers/{id}/redis-acl/issue``），跑在验签
之后：验签能过就说明 ``load_worker_secret`` 刚在库里查到过这个 ``public_id``，因此
这里落空只剩"验签之后 Worker 被删"的竞态——与验签层的 ``IDENTITY_UNKNOWN`` 是同一
件事的第二种表达，只是晚一次查询才被观测到。

它以前抛无码 401「Worker 不存在」。Worker 侧 ``control_plane_rejection`` 只读
``data.error_code``、绝不匹配文案，无码就落回 ``RuntimeError`` + 容器重启环——正是
``worker_auth_reasons`` 要消灭的那条故障链。

一正一反：身份不存在与 API Key 不符走同一个 401，两者必须给出**不同的**归因，
否则"能区分"这件事没有被证明——把后者说成身份丢失会让运维去清一份完全正常的凭据。
"""

import pytest
from antcode_core.common.security.api_key import hash_api_key
from antcode_core.common.security.worker_auth import WorkerAuthRejected
from antcode_core.common.security.worker_auth_reasons import WorkerAuthReason
from antcode_web_api.routes.v1 import workers as workers_route
from fastapi import HTTPException, status

WORKER_ID = "w-1"
EXPECTED_API_KEY = "expected-key"


class _BearerRequest:
    def __init__(self, api_key: str) -> None:
        self.headers = {"Authorization": f"Bearer {api_key}"}


def _patch_lookup(monkeypatch, worker) -> None:
    async def fake_get_worker_by_id(_worker_id):
        return worker

    monkeypatch.setattr(workers_route.worker_service, "get_worker_by_id", fake_get_worker_by_id)


def _known_worker():
    return type(
        "Worker",
        (),
        {
            "api_key_hash": hash_api_key(EXPECTED_API_KEY),
            "api_key_previous_hash": None,
            "api_key_previous_expires_at": None,
        },
    )()


async def _reject(request) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        await workers_route._verify_worker_credential_headers(request, {"worker_id": WORKER_ID})
    return exc_info.value


@pytest.mark.asyncio
async def test_unknown_worker_identity_carries_the_shared_identity_code(monkeypatch) -> None:
    _patch_lookup(monkeypatch, None)

    rejection = await _reject(_BearerRequest("any-key"))

    assert isinstance(rejection, WorkerAuthRejected)
    assert rejection.status_code == status.HTTP_401_UNAUTHORIZED
    assert rejection.error_code == WorkerAuthReason.IDENTITY_UNKNOWN.value
    assert "重新注册" in rejection.detail


@pytest.mark.asyncio
async def test_wrong_api_key_is_not_reported_as_a_lost_identity(monkeypatch) -> None:
    _patch_lookup(monkeypatch, _known_worker())

    rejection = await _reject(_BearerRequest("wrong-key"))

    assert rejection.status_code == status.HTTP_401_UNAUTHORIZED
    assert getattr(rejection, "error_code", None) != WorkerAuthReason.IDENTITY_UNKNOWN.value
    assert "API Key" in str(rejection.detail)


@pytest.mark.asyncio
async def test_matching_api_key_passes_through(monkeypatch) -> None:
    """成功臂：Worker 在库里且 API Key 正确，凭据校验不受归因改动影响。"""
    worker = _known_worker()
    _patch_lookup(monkeypatch, worker)

    context = await workers_route._verify_worker_credential_headers(
        _BearerRequest(EXPECTED_API_KEY),
        {"worker_id": WORKER_ID},
    )

    assert context["worker"] is worker
    assert context["auth_info"] == {"worker_id": WORKER_ID}
