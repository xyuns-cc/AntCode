"""Integration test fixtures."""

import os

import pytest
from antcode_core.common.settings_ref import current_settings

from tests.integration.redis_safety import (
    INTEGRATION_REDIS_URL_ENV,
    IntegrationRedisBindingError,
    assert_disposable_redis_binding,
    disposable_redis_targets,
)


def pytest_configure(config: pytest.Config) -> None:
    """本套件能写到的每个 Redis 都必须是声明过的一次性实例。

    放在 ``pytest_configure`` 而不是 fixture：守卫必须早于任何用例建连，
    并且一次性终止整轮 run；做成 fixture 会变成"每条用例各自报错"，
    第一条用例仍有机会在报错前写坏目标实例。
    """
    del config
    explicit_url = os.getenv(INTEGRATION_REDIS_URL_ENV, "").strip()
    if not explicit_url:
        # 没配 = 全部 Redis 集成用例按各模块既有 skipif 跳过，没有可保护的目标。
        return
    # settings.REDIS_URL 同样要查：test_fault_tolerance.py 驱动的真实 ResultLoop
    # 走 get_redis_client()，那条链只认生产变量，不认 ANTCODE_INTEGRATION_REDIS_URL。
    targets = disposable_redis_targets(explicit_url, current_settings().REDIS_URL)
    try:
        for target in targets:
            assert_disposable_redis_binding(target)
    except IntegrationRedisBindingError as exc:
        raise pytest.UsageError(str(exc)) from exc
