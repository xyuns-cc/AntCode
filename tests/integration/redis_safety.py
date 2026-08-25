"""``tests/integration`` 的一次性 Redis 实例绑定（fail-closed）。

为什么隔离必须落在**实例**上，而不是 namespace 或 db：

* ``tests/integration/worker/redis_acl_live_support.py`` 与
  ``test_redis_acl_live.py`` 把 ``namespace="antcode"`` 和 ``{antcode}:...``
  字面量写死给真 Redis，改 ``REDIS_NAMESPACE`` 搬不走这些键；
* 同一批用例跑 ``ACL SETUSER`` / ``ACL DELUSER``，ACL 是实例级配置，
  换 db 号一样挡不住。

为什么不照搬 PG 侧 ``assert_safe_database_name`` 的库名白名单：Redis 没有库名，
只有 db 号，URL 形状分不清"专用测试实例的 db 14"和"生产实例的 db 14"。所以判据
必须是运维在目标实例内部主动写下的绑定标记——生产实例不会有它，也不可能因为
环境变量写错而凑巧满足。范式取自 ``tests/loadtest/tool/binding.py``。

这套用例的破坏面：helper 会直接 ``SET`` Master 代际镜像
``{ns}:fencing:dispatch:master``。指向活栈时，在任 Master 的代际与镜像不再相等，
``publish_ready_batch`` 的 Lua 栅栏一律返回 ``scheduler_generation_changed``；
镜像只在重新选主（``FencingTokenManager.acquire_token``）时才会被重写，因此
在任 Master 会永久失去派发能力且不自愈。

目标不止一个：``ANTCODE_INTEGRATION_REDIS_URL`` 是显式测试目标，但
``test_fault_tolerance.py`` 会驱动真实 ``ResultLoop._handle_message`` →
``publish_persisted_run_status`` → ``publish_sse_event`` → ``get_redis_client()``，
那条链走的是生产变量 ``settings.REDIS_URL``，而且用默认 namespace 写 SSE 事件流。
两个目标都必须是声明过的一次性实例，否则守卫只是看起来生效。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import redis

from tests.loadtest.tool.config import masked_redis_url

INTEGRATION_REDIS_URL_ENV = "ANTCODE_INTEGRATION_REDIS_URL"
DISPOSABLE_BINDING_KEY = "antcode:integration-test:disposable-binding"
DISPOSABLE_BINDING_VALUE = "ANTCODE_INTEGRATION_TESTS_MAY_DESTROY_THIS_REDIS"
BINDING_SETUP_COMMAND = (
    f'redis-cli -u "${INTEGRATION_REDIS_URL_ENV}" SET {DISPOSABLE_BINDING_KEY} {DISPOSABLE_BINDING_VALUE}'
)

_BINDING_TIMEOUT_SECONDS = 5

RedisFactory = Callable[..., Any]


class IntegrationRedisBindingError(RuntimeError):
    """目标 Redis 没有声明成一次性测试实例，集成测试必须被拒绝。"""


def disposable_redis_targets(explicit_url: str, control_plane_url: str) -> tuple[str, ...]:
    """列出本套件真正能写到的 Redis 目标，去重保序。

    ``control_plane_url`` 为空时不列入：此时 ``get_redis_client()`` 会直接抛
    "REDIS_URL 未配置"，是响亮失败而不是打瘫某个实例，没有可保护的目标。
    """
    explicit = explicit_url.strip()
    control_plane = control_plane_url.strip()
    if not control_plane or control_plane == explicit:
        return (explicit,)
    return (explicit, control_plane)


def assert_disposable_redis_binding(url: str, *, redis_factory: RedisFactory | None = None) -> None:
    """目标 Redis 必须携带一次性实例绑定标记，否则明确拒绝并说明如何改对。"""
    target = url.strip()
    if not target:
        raise IntegrationRedisBindingError(f"{INTEGRATION_REDIS_URL_ENV} 为空，无法校验一次性实例绑定")
    actual = _read_binding(target, redis_factory or redis.Redis.from_url)
    if actual == DISPOSABLE_BINDING_VALUE:
        return
    raise IntegrationRedisBindingError(_rejection_message(target, actual))


def _read_binding(url: str, factory: RedisFactory) -> Any:
    client = factory(
        url,
        decode_responses=True,
        socket_connect_timeout=_BINDING_TIMEOUT_SECONDS,
        socket_timeout=_BINDING_TIMEOUT_SECONDS,
    )
    try:
        return client.get(DISPOSABLE_BINDING_KEY)
    except redis.RedisError as exc:
        # 读不到判据一律当"未绑定"处理：连不上时放行等于把守卫做成静默 fallback。
        raise IntegrationRedisBindingError(
            f"无法在 {masked_redis_url(url)} 上校验一次性实例绑定标记，拒绝运行 tests/integration: {exc}"
        ) from exc
    finally:
        client.close()


def _rejection_message(url: str, actual: Any) -> str:
    seen = "缺失" if actual is None else f"实际值为 {actual!r}"
    return "\n".join(
        (
            f"拒绝对 {masked_redis_url(url)} 运行 tests/integration："
            f"绑定标记 {DISPOSABLE_BINDING_KEY} {seen}，该实例没有被声明为一次性测试实例。",
            "tests/integration 会 SET Master 代际镜像 {ns}:fencing:dispatch:master、"
            "改写 Lease/heartbeat/ready stream，并对实例执行 ACL SETUSER/DELUSER；"
            "指向活栈会让在任 Master 永久无法派发，且不自愈。",
            "改 REDIS_NAMESPACE 或换 db 号都无效：ACL 是实例级配置，"
            "test_redis_acl_live.py 还把 antcode namespace 写死，隔离只能换实例。",
            f"确认目标 Redis 可以被销毁之后，在该实例上执行：\n  {BINDING_SETUP_COMMAND}",
            "严禁在生产或共享栈的 Redis 上写这个标记。",
        )
    )


__all__ = [
    "BINDING_SETUP_COMMAND",
    "DISPOSABLE_BINDING_KEY",
    "DISPOSABLE_BINDING_VALUE",
    "INTEGRATION_REDIS_URL_ENV",
    "IntegrationRedisBindingError",
    "assert_disposable_redis_binding",
    "disposable_redis_targets",
]
