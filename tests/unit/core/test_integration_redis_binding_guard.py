"""``tests/integration`` 指向活栈就会打瘫它，守卫必须 fail-closed。

真实事故链（已在一次性实例上复现）：``direct_transport_support.publish_ready_task``
会 ``SET {ns}:fencing:dispatch:master``。指向活栈时镜像被顶成测试代际，
``publish_ready_batch`` 的 Lua 栅栏对在任 Master 一律返回
``scheduler_generation_changed``；镜像只在重新选主时才重写，所以在任 Master
永久失去派发能力且不自愈。

PG 侧有 ``assert_safe_database_name`` 挡住指错库，Redis 侧此前什么都没有。
本模块钉住补上的那一层，包括它必须覆盖的第二个目标 ``settings.REDIS_URL``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import redis

from tests.integration import conftest as integration_conftest
from tests.integration.redis_safety import (
    BINDING_SETUP_COMMAND,
    DISPOSABLE_BINDING_KEY,
    DISPOSABLE_BINDING_VALUE,
    INTEGRATION_REDIS_URL_ENV,
    IntegrationRedisBindingError,
    assert_disposable_redis_binding,
    disposable_redis_targets,
)

PRODUCTION_LOOKING_URL = "redis://10.0.0.9:6379/0"
LAB_URL = "redis://127.0.0.1:16999/0"
SOURCE_ROOTS = ("packages", "services", "scripts", "tests")
GUARDED_PREFIX = "tests/integration/"


@dataclass(frozen=True)
class _StubSettings:
    REDIS_URL: str


class _StubRedis:
    def __init__(self, value: object | Exception) -> None:
        self._value = value
        self.closed = False
        self.requested_keys: list[str] = []

    def get(self, key: str) -> object:
        self.requested_keys.append(key)
        if isinstance(self._value, Exception):
            raise self._value
        return self._value

    def close(self) -> None:
        self.closed = True


def _factory(value: object | Exception):
    created: list[_StubRedis] = []

    def build(url: str, **kwargs: object) -> _StubRedis:
        del url, kwargs
        client = _StubRedis(value)
        created.append(client)
        return client

    return build, created


def test_rejects_production_looking_redis_without_binding_marker() -> None:
    build, created = _factory(None)

    with pytest.raises(IntegrationRedisBindingError) as excinfo:
        assert_disposable_redis_binding(PRODUCTION_LOOKING_URL, redis_factory=build)

    message = str(excinfo.value)
    assert DISPOSABLE_BINDING_KEY in message
    assert BINDING_SETUP_COMMAND in message
    assert created[0].requested_keys == [DISPOSABLE_BINDING_KEY]
    assert created[0].closed


def test_rejects_redis_carrying_a_different_binding_value() -> None:
    build, _ = _factory("yes")

    with pytest.raises(IntegrationRedisBindingError, match="没有被声明为一次性测试实例"):
        assert_disposable_redis_binding(PRODUCTION_LOOKING_URL, redis_factory=build)


def test_unreadable_binding_is_rejected_instead_of_silently_allowed() -> None:
    """连不上就放行 = 静默 fallback，正是本仓禁止的那类"为了跑起来"的降级。"""
    build, created = _factory(redis.RedisError("connection refused"))

    with pytest.raises(IntegrationRedisBindingError, match="拒绝运行 tests/integration"):
        assert_disposable_redis_binding(PRODUCTION_LOOKING_URL, redis_factory=build)

    assert created[0].closed


def test_accepts_instance_declared_disposable() -> None:
    build, created = _factory(DISPOSABLE_BINDING_VALUE)

    assert_disposable_redis_binding(LAB_URL, redis_factory=build)

    assert created[0].closed


def test_empty_url_is_rejected_by_the_assertion_itself() -> None:
    with pytest.raises(IntegrationRedisBindingError, match=INTEGRATION_REDIS_URL_ENV):
        assert_disposable_redis_binding("   ")


def test_control_plane_redis_is_a_separate_target_that_must_also_be_bound() -> None:
    """test_fault_tolerance.py 的真实 ResultLoop 只认 settings.REDIS_URL。"""
    assert disposable_redis_targets(LAB_URL, PRODUCTION_LOOKING_URL) == (LAB_URL, PRODUCTION_LOOKING_URL)


def test_identical_control_plane_target_is_only_checked_once() -> None:
    assert disposable_redis_targets(LAB_URL, f" {LAB_URL} ") == (LAB_URL,)


def test_unset_control_plane_url_is_not_a_target() -> None:
    """REDIS_URL 为空时 get_redis_client() 直接报错，是响亮失败而不是隐患。"""
    assert disposable_redis_targets(LAB_URL, "") == (LAB_URL,)


def test_conftest_checks_the_control_plane_redis_too(monkeypatch) -> None:
    monkeypatch.setenv(INTEGRATION_REDIS_URL_ENV, LAB_URL)
    monkeypatch.setattr(integration_conftest, "current_settings", lambda: _StubSettings(PRODUCTION_LOOKING_URL))
    checked: list[str] = []
    monkeypatch.setattr(integration_conftest, "assert_disposable_redis_binding", checked.append)

    integration_conftest.pytest_configure(None)

    assert checked == [LAB_URL, PRODUCTION_LOOKING_URL]


def test_conftest_turns_a_rejected_target_into_a_usage_error(monkeypatch) -> None:
    monkeypatch.setenv(INTEGRATION_REDIS_URL_ENV, PRODUCTION_LOOKING_URL)
    monkeypatch.setattr(integration_conftest, "current_settings", lambda: _StubSettings(""))

    def reject(url: str) -> None:
        raise IntegrationRedisBindingError(f"rejected {url}")

    monkeypatch.setattr(integration_conftest, "assert_disposable_redis_binding", reject)

    with pytest.raises(pytest.UsageError, match=PRODUCTION_LOOKING_URL):
        integration_conftest.pytest_configure(None)


def test_conftest_is_inert_when_the_target_is_not_configured(monkeypatch) -> None:
    monkeypatch.delenv(INTEGRATION_REDIS_URL_ENV, raising=False)

    def explode(url: str) -> None:
        raise AssertionError(f"未配置目标时不应建连: {url}")

    monkeypatch.setattr(integration_conftest, "assert_disposable_redis_binding", explode)

    integration_conftest.pytest_configure(None)


def test_every_integration_redis_consumer_sits_under_the_guarded_conftest() -> None:
    """非证伪项：钉住守卫的覆盖面，防止新用例在 conftest 管辖之外读同一个环境变量。"""
    this_file = Path(__file__).resolve()
    offenders = [
        path.as_posix()
        for root in SOURCE_ROOTS
        for path in Path(root).rglob("*.py")
        if path.resolve() != this_file
        and not path.as_posix().startswith(GUARDED_PREFIX)
        and INTEGRATION_REDIS_URL_ENV in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        f"这些模块读 {INTEGRATION_REDIS_URL_ENV} 却不受 tests/integration/conftest.py 守卫: {offenders}"
    )
