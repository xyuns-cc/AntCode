"""
AntCode Master 主入口

将原本顺序启动的若干 loop 拆成两组并行启动：

- ``control`` 子模块：调度、补偿、协调、选主等控制面 loop，
  使用 *cold* Redis 连接池（低频、连接数少）。
- ``ingester`` 子模块：结果消费、日志落库等数据面 loop，
  使用 *hot* Redis 连接池（高吞吐、连接数大）。

两组之间通过 ``asyncio.gather`` 并行启动，组内各 loop 也并行启动，
让控制面不至于被数据面阻塞。
"""

import asyncio
import sys
from collections.abc import Awaitable
from typing import cast

from antcode_core.application.services.lease_service import LeaseStore, wire_lease_policy
from antcode_core.application.services.scheduler.outbox_service import (
    scheduler_outbox_service,
)
from antcode_core.common.config import settings
from antcode_core.common.logging import setup_logging
from antcode_core.infrastructure.db.tortoise import close_db, init_db
from antcode_core.infrastructure.redis import (
    close_redis_pool,
    get_redis_client,
    make_cold_client,
    redis_namespace,
)
from loguru import logger

from antcode_master.control.lease_sweeper_loop import LeaseSweeperLoop
from antcode_master.control.reconcile_loop import reconcile_loop
from antcode_master.control.redispatch_loop import redispatch_loop
from antcode_master.control.retry_loop import retry_service
from antcode_master.control.scheduler_event_loop import scheduler_event_loop
from antcode_master.control.scheduler_loop import scheduler_service
from antcode_master.control.worker_eviction import configure_lease_store, on_worker_evicted
from antcode_master.ingester.alert_check_loop import alert_check_loop
from antcode_master.ingester.artifact_cleanup_loop import artifact_cleanup_loop
from antcode_master.ingester.crawl_batch_status_loop import crawl_batch_status_loop
from antcode_master.ingester.log_ingest_loop import log_ingest_loop
from antcode_master.ingester.result_loop import result_loop
from antcode_master.ingester.worker_registration_cleanup_loop import worker_registration_cleanup_loop
from antcode_master.leader import leader_election
from antcode_master.process_signals import install_stop_signal_handlers
from antcode_master.readiness import master_readiness

# P3:LeaseStore + LeaseSweeperLoop 单例持有,便于 stop_master 关闭
_lease_store: LeaseStore | None = None
_lease_sweeper: LeaseSweeperLoop | None = None


# 可选的 cold Redis client（P5.2）。当前 control 组的 loop 默认仍走 StreamClient
# 的单例 hot pool；保留这个引用便于未来控制面需要直接拿低频客户端时使用，
# 同时让 ``__main__`` 在启动阶段就显式构造一次，验证配置正确。
_cold_redis_client = None


async def _start_control_group() -> None:
    """启动控制面 loop 组（scheduler / event / reconcile / retry / lease sweeper）"""
    global _lease_store, _lease_sweeper
    logger.info("[control] 启动控制面 loop 组")

    # P3:初始化 LeaseStore + LeaseSweeperLoop(每秒扫过期 lease,触发失租回调)
    if _lease_store is None:
        redis_client = await get_redis_client()
        # 显式传 namespace：sweeper 必须与 gateway/worker 落在同一 keyspace，
        # 否则非默认 REDIS_NAMESPACE 部署时会扫到空集，死 worker 永不剔除。
        _lease_store = LeaseStore(redis_client, namespace=redis_namespace(), policy=wire_lease_policy())
    configure_lease_store(_lease_store)
    if _lease_sweeper is None:
        _lease_sweeper = LeaseSweeperLoop(
            lease_store=_lease_store,
            on_worker_evicted=on_worker_evicted,
        )

    await asyncio.gather(
        scheduler_service.start(),
        scheduler_event_loop.start(),
        scheduler_outbox_service.start(),
        reconcile_loop.start(),
        retry_service.start(),
        _lease_sweeper.start(),
        # T7-B3a: 派发失败自动补派 loop（leader-only；gate 在 loop 内部）
        redispatch_loop.start(),
    )
    logger.info("[control] 控制面 loop 组已就绪")


async def _start_ingester_group() -> None:
    """启动数据面 loop 组（result / log / artifact cleanup）"""
    logger.info("[ingester] 启动数据面 loop 组")
    await asyncio.gather(
        result_loop.start(),
        log_ingest_loop.start(),
        artifact_cleanup_loop.start(),
        crawl_batch_status_loop.start(),
        alert_check_loop.start(),
        worker_registration_cleanup_loop.start(),
    )
    logger.info("[ingester] 数据面 loop 组已就绪")


async def start_master() -> None:
    """启动 Master 服务"""
    global _cold_redis_client

    settings.SCHEDULER_ROLE = "master"
    logger.info(f"启动 Master 调度服务 v{settings.APP_VERSION}")

    # 1. 预热 Redis 连接池
    #    - hot pool：通过 get_redis_client() 触发单例初始化，默认 50 连接，
    #      之后所有 StreamClient() 默认构造都会复用它（数据面）。
    #    - cold pool：显式创建一个独立的低连接数客户端，预留给控制面使用。
    logger.info("[0/6] 预热 Redis 连接池 (hot + cold)")
    try:
        await get_redis_client()
        _cold_redis_client = make_cold_client()
        # 简单 ping 验证 cold pool 可用
        await cast(Awaitable[bool], _cold_redis_client.ping())
        logger.info("Redis hot/cold 连接池已就绪")
    except Exception as e:
        raise RuntimeError("Redis 连接池预热失败") from e

    await _sync_worker_acls()

    # 2. 尝试成为 Leader
    logger.info("[1/6] 尝试成为 Leader")
    if await leader_election.try_become_leader():
        logger.info(f"已成为 Leader, token={leader_election.fencing_token}")
    else:
        logger.info("未成为 Leader，将在后台持续尝试")

    # 3. 恢复中断任务（控制面初始化前的一次性动作）
    logger.info("[2/6] 恢复中断任务")
    # P1-FN-13: 启动恢复失败改 fatal（判死已保守跳过；其他非预期错直接 raise 让 orchestration 重启）。
    from antcode_master.task_persistence import task_recovery_service

    stats = await task_recovery_service.recover_on_startup()
    if stats["recovered"] > 0:
        logger.info(f"已恢复 {stats['recovered']} 个中断任务")

    # L4: 显式初始化 alert_service，让告警通道配置从 DB 加载，
    # 而不是等第一次 send_alert 时懒加载（此前 crawl 阈值告警会因通道未
    # ready 而丢弃）
    try:
        from antcode_core.application.services.alert.alert_service import (
            alert_service,
        )

        await alert_service.initialize()
        logger.info("alert_service 已初始化")
    except Exception as e:
        logger.warning(f"alert_service 初始化失败（非致命）: {e}")

    # L5: 订阅 system_config 失效通道；web_api 改配置后 master 也能 reload 缓存
    try:
        from antcode_core.application.services.system_config import (
            system_config_service,
        )

        # 首次加载（notify=False 因为初始加载不需要 fanout）
        await system_config_service.reload_config_cache(notify=False)
        await system_config_service.start_invalidation_subscriber()
    except Exception as e:
        logger.warning(f"system_config 订阅启动失败（非致命）: {e}")

    # 4. 并行启动 control / ingester 两组 loop
    logger.info("[3/6] 并行启动 control + ingester loop 组")
    results = await asyncio.gather(
        _start_control_group(),
        _start_ingester_group(),
        return_exceptions=True,
    )
    for group_name, result in zip(("control", "ingester"), results):
        if isinstance(result, Exception):
            raise RuntimeError(f"{group_name} 组启动失败") from result

    # P1-FN-05: scheduler leader 激活失败必须让 readiness 转 503。
    master_readiness.add_probe(lambda: not scheduler_service.activation_failed)
    await master_readiness.start()
    logger.info("Master 服务已启动")


async def _stop_control_group() -> None:
    """停止控制面 loop 组"""
    global _lease_sweeper
    coros = [
        reconcile_loop.stop(),
        scheduler_event_loop.stop(),
        scheduler_outbox_service.stop(),
        scheduler_service.shutdown(),
        retry_service.stop(),
        redispatch_loop.stop(),
    ]
    if _lease_sweeper is not None:
        coros.append(_lease_sweeper.stop())
    await asyncio.gather(*coros, return_exceptions=True)
    _lease_sweeper = None


async def _stop_ingester_group() -> None:
    """停止数据面 loop 组"""
    await asyncio.gather(
        result_loop.stop(),
        log_ingest_loop.stop(),
        artifact_cleanup_loop.stop(),
        crawl_batch_status_loop.stop(),
        alert_check_loop.stop(),
        worker_registration_cleanup_loop.stop(),
        return_exceptions=True,
    )


async def stop_master() -> None:
    """停止 Master 服务"""
    global _cold_redis_client

    logger.info("正在停止 Master 服务...")
    await master_readiness.stop()

    # 1. 并行停止两组 loop
    results = await asyncio.gather(
        _stop_control_group(),
        _stop_ingester_group(),
        return_exceptions=True,
    )
    for group_name, result in zip(("control", "ingester"), results):
        if isinstance(result, Exception):
            logger.error(f"停止 {group_name} 组失败: {result}")
        else:
            logger.info(f"{group_name} 组已停止")

    # 2. 放弃 Leader 身份
    try:
        await leader_election.step_down()
        logger.info("已放弃 Leader 身份")
    except Exception as e:
        logger.error(f"放弃 Leader 身份失败: {e}")

    # 3. 关闭 cold Redis client（hot pool 由 close_db 之后的 close_redis_pool 处理）
    if _cold_redis_client is not None:
        try:
            await _cold_redis_client.close()
        except Exception as e:
            logger.error(f"关闭 cold Redis client 失败: {e}")
        _cold_redis_client = None

    try:
        await close_redis_pool()
    except Exception as e:
        logger.error(f"关闭 hot Redis pool 失败: {e}")

    logger.info("Master 服务已停止")


async def _sync_worker_acls() -> None:
    if not settings.REDIS_ACL_ENABLED:
        return
    from antcode_core.common.security.redis_acl import sync_all_worker_acls

    redis_client = await get_redis_client()
    if redis_client is None:
        raise RuntimeError("Redis ACL 同步失败: Redis client unavailable")
    await sync_all_worker_acls(redis_client)


async def main() -> None:
    """主函数"""
    setup_logging()

    # P2-a2: 必须传 service="master",否则 helper 走 default 池,
    # DB_POOL_MAX_MASTER 配置将静默失效。
    await init_db(service="master")

    stop_event = install_stop_signal_handlers()

    try:
        await start_master()
        await stop_event.wait()
    except KeyboardInterrupt:
        logger.info("收到键盘中断")
    except Exception as e:
        logger.error(f"Master 服务异常: {e}")
        sys.exit(1)
    finally:
        await stop_master()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
