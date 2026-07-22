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
import signal
import sys
from collections.abc import Awaitable
from typing import cast

from antcode_core.application.services.lease_service import LeasePolicy, LeaseStore
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
from antcode_master.ingester.alert_check_loop import alert_check_loop
from antcode_master.ingester.artifact_cleanup_loop import artifact_cleanup_loop
from antcode_master.ingester.crawl_batch_status_loop import crawl_batch_status_loop
from antcode_master.ingester.log_ingest_loop import log_ingest_loop
from antcode_master.ingester.result_loop import result_loop
from antcode_master.ingester.worker_registration_cleanup_loop import worker_registration_cleanup_loop
from antcode_master.leader import leader_election
from antcode_master.readiness import master_readiness

# P3:LeaseStore + LeaseSweeperLoop 单例持有,便于 stop_master 关闭
_lease_store: LeaseStore | None = None
_lease_sweeper: LeaseSweeperLoop | None = None


async def _on_worker_evicted(worker_id: str, evicted_lease_id: str = "") -> None:
    """只回收绑定被淘汰 Lease 的 TaskRun；新代际不受影响。

    ``evicted_lease_id`` 为空（lease Hash 已被 PEXPIRE 提前清掉，sweeper
    停摆超过 retention 窗口时必然发生）时不能放弃 eviction：仍按"代际
    未知"路径下线 Worker 并回收其 RUNNING TaskRun，否则死 Worker 会永远
    ONLINE、RUNNING run 只能等 reconcile 慢超时兜底。
    """
    await _apply_worker_eviction(worker_id, evicted_lease_id)


async def _eviction_was_superseded(worker_id: str, evicted_lease_id: str) -> bool:
    """当前 Worker 是否已持有**另一份有效**（未逻辑过期）的 lease。

    只有 ``current.lease_id`` 非空、与被淘汰代际不同、且未过期时才算被
    取代；过期残留记录（retention 窗口内的 Hash）或同代际残留不算。
    """
    if _lease_store is None:
        raise RuntimeError("LeaseStore 尚未初始化，拒绝执行 eviction 副作用")
    current = await _lease_store.get(worker_id, include_expired=False)
    if current is None or not current.lease_id:
        return False
    if evicted_lease_id and current.lease_id == evicted_lease_id:
        return False
    logger.info(
        "忽略旧 Lease eviction（worker 已持有新代际有效 lease）: worker_id={}",
        worker_id,
    )
    return True


async def _apply_worker_eviction(worker_id: str, evicted_lease_id: str) -> None:
    from datetime import UTC, datetime

    from antcode_core.application.services.scheduler.execution_status_service import (
        execution_status_service,
    )
    from antcode_core.domain.models import TaskRun, Worker
    from antcode_core.domain.models.enums import RuntimeStatus, TaskStatus, WorkerStatus
    from tortoise.expressions import Q

    worker = await Worker.filter(public_id=worker_id).first()
    if worker is None:
        return
    superseded = await _eviction_was_superseded(worker_id, evicted_lease_id)
    if superseded and not evicted_lease_id:
        # 代际未知且 worker 已持有新的有效 lease：没有任何可安全 fencing 的
        # TaskRun 集合，整体跳过，交由新代际的 eviction / reconcile 处理。
        logger.info(
            "跳过代际未知的 eviction（worker 已持有有效 lease）: worker_id={}",
            worker_id,
        )
        return
    query = TaskRun.filter(worker_id=worker.id, status=TaskStatus.RUNNING)
    if superseded:
        # worker 已换新代际：只回收明确绑定被淘汰代际的 run；lease_id 为
        # NULL 的 run 无法区分新旧代际，不动。
        query = query.filter(lease_id=evicted_lease_id)
    elif evicted_lease_id:
        # lease_id 懒绑定 / report-task 可先把 run 翻成 RUNNING 而 lease_id
        # 仍为 NULL——这类 run 同样属于失联 worker，必须一起回收；绑定其它
        # （更新）代际的 run 仍然排除。
        query = query.filter(Q(lease_id=evicted_lease_id) | Q(lease_id__isnull=True))
    # else: 代际未知且 worker 无任何有效 lease → 该 worker 全部 RUNNING run
    # 都已失联，不做 lease 过滤。
    running = await query.only("id", "run_id", "lease_id").all()

    # V1 over-eviction 竞态防护：``superseded`` 由本次 eviction 早先的**单次**
    # ``_lease_store.get()`` 决定。在 "L1 过期 → sweep 剔除 → worker 又以新代际
    # L2 回归" 的窗口里,该判定可能停在 L2 grant 之前(superseded=False),于是上面
    # 把 lease_id 为 NULL 的 RUNNING run 也纳入了回收集——而这些 run 可能正由已在
    # L2 下复活的存活 worker 执行,误标 FAILED 会造成重复/冲突完成。
    #
    # 仅在 "非 superseded + 已知代际"(即把 NULL-lease run 纳入回收)的分支里,贴近
    # UPDATE 再复核一次 lease store:若 worker 此刻已持有另一份有效代际,说明它确实
    # 回来了,跳过 NULL-lease run(交由 reconcile 超时或下一次 L2 sweep eviction
    # 兜底),把 TOCTOU 窗口收敛到接近零。绑定被淘汰代际的 run 属于已死代际,始终
    # 无条件回收。
    skip_null_lease = False
    if not superseded and evicted_lease_id and any(not t.lease_id for t in running):
        if await _eviction_was_superseded(worker_id, evicted_lease_id):
            skip_null_lease = True
            logger.info(
                "失效前复核发现 worker 已以新代际回归,跳过 NULL-lease run 回收: worker_id={}",
                worker_id,
            )

    # worker 已在新代际下复活(skip_null_lease)时不应被下线——与 superseded 分支一致。
    if not superseded and not skip_null_lease and worker.status == WorkerStatus.ONLINE.value:
        worker.status = WorkerStatus.OFFLINE.value
        await worker.save(update_fields=["status"])
    now = datetime.now(UTC)
    failed = 0
    skipped = 0
    for task in running:
        if skip_null_lease and not task.lease_id:
            # worker 已在 L2 回归,这条 NULL-lease run 可能仍存活,不做 fencing。
            skipped += 1
            continue
        ok = await execution_status_service.update_runtime_status(
            run_id=task.run_id,
            status=RuntimeStatus.FAILED,
            status_at=now,
            error_message="Worker 失联（lease expired）",
            # 逐 run 用其自身代际做 fencing；lease_id 为 NULL 的 run 无代际
            # 可比（传 None 跳过 lease 比对），仍受 runtime 状态 CAS 保护。
            expected_lease_id=task.lease_id or None,
        )
        failed += int(ok)
    logger.info(
        "Lease 失租回收完成: worker_id={} marked_failed={}/{} skipped_null={}",
        worker_id,
        failed,
        len(running),
        skipped,
    )


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
        _lease_store = LeaseStore(redis_client, namespace=redis_namespace(), policy=LeasePolicy())
    if _lease_sweeper is None:
        _lease_sweeper = LeaseSweeperLoop(
            lease_store=_lease_store,
            on_worker_evicted=_on_worker_evicted,
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
    # P1-FN-13: 启动恢复失败改为 fatal(原 warn 后继续,让 Master 半启动)。
    # recover_on_startup 内部的判死逻辑本身已经在 Lease store 不可达时保守
    # 跳过(见 task_persistence._get_active_worker_ids 返回 None 的分支),
    # 走到这个 except 说明遇到的是数据库/序列化/其他非预期错误——不该让
    # Master 带残破恢复状态继续跑,直接 raise 让 orchestration 层重启。
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

    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()
    stopping = False

    def signal_handler() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        logger.info("收到停止信号")
        stop_event.set()

    # P2-14: SIGTERM/SIGINT 都注册了 handler,K8s 优雅关停时 stop_event.set()
    # 会触发 stop_master() drain(停 loop + step_down leader + 关连接池)。
    # Windows 上 asyncio 不支持 add_signal_handler，改用 signal.signal。
    if sys.platform == "win32":

        def _win_handler(signum, frame):  # noqa: ARG001
            loop.call_soon_threadsafe(signal_handler)

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, _win_handler)
    else:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)

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
