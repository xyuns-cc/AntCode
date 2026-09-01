"""告警配置的跨进程失效广播。

web_api 以 ``SERVER_WORKERS>1`` 多进程运行，而 ``alert_manager`` 是进程内单例：
只有恰好处理 ``PUT /alert/config`` 的那个 uvicorn worker 会重建渠道，兄弟进程会
一直拿旧配置回答 ``GET /alert/config`` 和 ``POST /alert/test``（实测同一份配置在
两个进程间随机跳）。这里沿用 ``system_config_service`` 已有的 Redis pubsub 失效
范式：写入方 publish，其余进程订阅后各自从 PostgreSQL 重载。

失败一律显式抛出/显式 ERROR 日志，不做静默降级——订阅断掉意味着本进程从此
持有旧配置，必须让它可见。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from loguru import logger

from antcode_core.infrastructure.redis import get_redis_client, redis_namespace


def alert_config_invalidation_channel() -> str:
    """失效通道名，跟随 ``REDIS_NAMESPACE``。

    共用一台 Redis 的多套部署正是靠 REDIS_NAMESPACE 隔离，而 pubsub 频道不分
    库、全实例可见：写死 ``antcode:`` 时 A 的一次配置写入会唤醒 B 的每个进程去
    重载它自己的库。拼接方式与 ``control_plane`` 的其余 key 一致，不另起一份。
    """
    return f"{redis_namespace()}:alert_config:invalidate"


# redis-py 的 pubsub.listen() 会先吐 subscribe/unsubscribe 确认帧，只有这两类是数据
_PAYLOAD_MESSAGE_TYPES = frozenset({"message", "pmessage"})

ReloadCallback = Callable[[], Awaitable[None]]

# asyncio 只对运行中的 Task 持弱引用；不留强引用会被 GC 静默回收，订阅随之失效。
_subscriber_tasks: set[asyncio.Task] = set()


async def publish_alert_config_invalidation() -> None:
    """通知其它进程重载告警配置。写入方（配置更新接口）调用。"""
    redis = await get_redis_client()
    await redis.publish(alert_config_invalidation_channel(), "1")
    logger.debug("已发布告警配置失效通知")


async def start_alert_config_subscriber(reload: ReloadCallback) -> None:
    """订阅失效通道。每个进程（含每个 uvicorn worker）启动时调用一次。

    ``reload`` 必须是不再广播的重载，否则订阅端会把通知打回去形成回环。
    """
    redis = await get_redis_client()
    pubsub = redis.pubsub()
    await pubsub.subscribe(alert_config_invalidation_channel())
    task = asyncio.create_task(_consume(pubsub, reload))
    _subscriber_tasks.add(task)
    task.add_done_callback(_on_subscriber_exit)
    logger.info("告警配置失效订阅已启动")


async def _consume(pubsub, reload: ReloadCallback) -> None:
    async for message in pubsub.listen():
        if message.get("type") not in _PAYLOAD_MESSAGE_TYPES:
            continue
        await reload()
        logger.info("收到告警配置失效通知，本进程告警渠道已重载")


def _on_subscriber_exit(task: asyncio.Task) -> None:
    _subscriber_tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(f"告警配置失效订阅已退出，本进程之后将持有旧告警配置: {error!r}")


__all__ = [
    "alert_config_invalidation_channel",
    "publish_alert_config_invalidation",
    "start_alert_config_subscriber",
]
