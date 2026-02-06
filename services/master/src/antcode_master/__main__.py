"""
AntCode Master 主入口

启动 Master 调度服务
"""

import asyncio
import signal
import sys

from loguru import logger

from antcode_core.common.config import settings
from antcode_core.common.logging import setup_logging
from antcode_core.infrastructure.db.tortoise import close_db, init_db
from antcode_master.leader import leader_election
from antcode_master.loops.reconcile_loop import reconcile_loop


async def start_master():
    """启动 Master 服务"""
    settings.SCHEDULER_ROLE = "master"
    logger.info(f"启动 Master 调度服务 v{settings.APP_VERSION}")

    # 1. 尝试成为 Leader
    logger.info("[1/6] 尝试成为 Leader")
    if await leader_election.try_become_leader():
        logger.info(f"已成为 Leader, token={leader_election.fencing_token}")
    else:
        logger.info("未成为 Leader，将在后台持续尝试")

    # 2. 启动调度循环
    logger.info("[2/7] 启动调度循环")
    try:
        from antcode_master.loops.scheduler_loop import scheduler_service
        await scheduler_service.start()
        logger.info("调度循环已启动")
    except Exception as e:
        logger.error(f"调度循环启动失败: {e}")

    # 3. 恢复中断任务
    logger.info("[3/7] 恢复中断任务")
    try:
        from antcode_master.task_persistence import task_recovery_service

        stats = await task_recovery_service.recover_on_startup()
        if stats["recovered"] > 0:
            logger.info(f"已恢复 {stats['recovered']} 个中断任务")
    except Exception as e:
        logger.warning(f"任务恢复失败（非致命）: {e}")

    # 4. 启动调度事件循环
    logger.info("[4/7] 启动调度事件循环")
    try:
        from antcode_master.loops.scheduler_event_loop import scheduler_event_loop

        await scheduler_event_loop.start()
        logger.info("调度事件循环已启动")
    except Exception as e:
        logger.error(f"调度事件循环启动失败: {e}")

    # 5. 启动协调循环
    logger.info("[5/7] 启动协调循环")
    try:
        await reconcile_loop.start()
        logger.info("协调循环已启动")
    except Exception as e:
        logger.error(f"协调循环启动失败: {e}")

    # 6. 启动重试循环
    logger.info("[6/7] 启动重试循环")
    try:
        from antcode_master.loops.retry_loop import retry_service
        await retry_service.start()
        logger.info("重试循环已启动")
    except Exception as e:
        logger.error(f"重试循环启动失败: {e}")

    # 7. 启动结果消费循环
    logger.info("[7/7] 启动结果消费循环")
    try:
        from antcode_master.loops.result_loop import result_loop
        await result_loop.start()
        logger.info("结果消费循环已启动")
    except Exception as e:
        logger.error(f"结果消费循环启动失败: {e}")

    logger.info("Master 服务已启动")


async def stop_master():
    """停止 Master 服务"""
    logger.info("正在停止 Master 服务...")

    # 停止协调循环
    try:
        await reconcile_loop.stop()
        logger.info("协调循环已停止")
    except Exception as e:
        logger.error(f"停止协调循环失败: {e}")

    # 停止调度事件循环
    try:
        from antcode_master.loops.scheduler_event_loop import scheduler_event_loop

        await scheduler_event_loop.stop()
        logger.info("调度事件循环已停止")
    except Exception as e:
        logger.error(f"停止调度事件循环失败: {e}")

    # 停止调度循环
    try:
        from antcode_master.loops.scheduler_loop import scheduler_service
        await scheduler_service.shutdown()
        logger.info("调度循环已停止")
    except Exception as e:
        logger.error(f"停止调度循环失败: {e}")

    # 停止重试循环
    try:
        from antcode_master.loops.retry_loop import retry_service
        await retry_service.stop()
        logger.info("重试循环已停止")
    except Exception as e:
        logger.error(f"停止重试循环失败: {e}")

    # 停止结果消费循环
    try:
        from antcode_master.loops.result_loop import result_loop
        await result_loop.stop()
        logger.info("结果消费循环已停止")
    except Exception as e:
        logger.error(f"停止结果消费循环失败: {e}")

    # 放弃 Leader 身份
    try:
        await leader_election.step_down()
        logger.info("已放弃 Leader 身份")
    except Exception as e:
        logger.error(f"放弃 Leader 身份失败: {e}")

    logger.info("Master 服务已停止")


async def main():
    """主函数"""
    setup_logging()

    await init_db()

    # 设置信号处理
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()
    stopping = False

    def signal_handler():
        nonlocal stopping
        if stopping:
            return
        stopping = True
        logger.info("收到停止信号")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await start_master()

        # 保持运行，直到收到停止信号
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
