"""REVOKE_LUA 的撤销集 TTL 不得被静默 clamp。

``REVOKE_LUA`` 曾对非法 ``revoked_ttl`` 写 ``revoked_ttl = 1``（同文件
``GRANT_LUA`` 对同类非法入参用的是 ``redis.error_reply``）。撤销集
``{ns}:lease:revoked:{worker_id}`` 若 1 秒后过期，``lease_fenced_ready_publish``
里的 ``SISMEMBER(revoked_key, lease_id)`` 撤销校验随之失效——已撤销的租约
会重新变得可派发。

仓库未安装 lupa / fakeredis，无法在单测里真跑 Lua；这里对脚本正文断言守卫
形态，并同时钉住"当前唯一调用方永远传得出正数"这一前提。
"""

from unittest.mock import MagicMock

import pytest
from antcode_core.application.services.lease_service import _REVOKE_LUA, LeasePolicy, LeaseStore


def test_revoke_lua_rejects_an_illegal_ttl_instead_of_clamping_it_to_one_second() -> None:
    assert "revoked_ttl = 1" not in _REVOKE_LUA
    assert "return redis.error_reply('lease revoked_ttl must be positive')" in _REVOKE_LUA


@pytest.mark.parametrize("ttl_ms", [0, 1, 1_000, 30_000, 3_600_000])
def test_revoked_ttl_argument_is_always_positive(ttl_ms: int) -> None:
    """当前唯一调用方不可能触发上面的错误分支：TTL 恒 ≥ margin。"""
    store = LeaseStore(MagicMock(), namespace="tenant", policy=LeasePolicy(ttl_ms=ttl_ms, renew_after_ms=0))

    assert store._revoked_ttl_seconds() >= LeaseStore.REVOKED_TTL_MARGIN_SECONDS
