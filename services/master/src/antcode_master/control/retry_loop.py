"""重试队列的唯一消费者 —— Master 控制循环。

**P1-18**:重试意图不再放进程内队列(Master 重启/切主会永久丢失),
权威标记是 ``TaskRun.next_retry_at``,Redis 只是投递通道
(队列协议见 ``antcode_core...scheduler.retry_queue``)。

写入侧 ``scheduler_loop._claim_retry_intent`` 先在 TaskRun 上原子创建
intent 再投递;消费侧必须先建出新 TaskRun、再清库、最后才 ACK
processing —— 任何一步失败都留着 intent 等下轮重来。

非 Leader 不消费:双 Master 下同时 trigger 会重复派发。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from antcode_core.application.services.scheduler.retry_queue import RetryQueueBackend
from antcode_core.domain.models.task_run import TaskRun
from loguru import logger

from antcode_master.control.retry_db_recovery import recover_retry_intents, terminate_durable_intent
from antcode_master.control.retry_intent_guard import (
    RetryIntent,
    RetryIntentInvalidError,
    RetryTargetInvalidError,
)

INFRASTRUCTURE_REQUEUE_DELAY_SECONDS = 5


class RetryClaimBusyError(RuntimeError):
    """Retry intent 无法立即消费(target task busy),需 requeue 但不算失败。"""


def _parse_retry_intent(item: dict[str, Any], source_run_id: str) -> RetryIntent:
    try:
        task_id = int(item["task_id"])
        retry_count = int(item["retry_count"])
        retry_time = datetime.fromisoformat(str(item["retry_time"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RetryIntentInvalidError(f"retry payload 字段非法: run_id={source_run_id}") from exc
    if task_id <= 0 or retry_count <= 0 or retry_time.tzinfo is None:
        raise RetryIntentInvalidError(f"retry payload 值非法: run_id={source_run_id}")
    return RetryIntent(task_id, source_run_id, retry_count, retry_time)


class RetryService:
    """任务重试服务"""

    def __init__(self):
        self._backend = RetryQueueBackend()
        self._running = False
        self._worker_task: asyncio.Task | None = None

    async def schedule_intent(self, intent) -> None:
        """将已经持久化到 source TaskRun 的 intent 投递到 Redis。"""
        await self._backend.schedule(
            task_id=intent.task_id,
            run_id=intent.source_run_id,
            retry_time=intent.retry_time,
            retry_count=intent.retry_count,
        )

    async def cancel_task(self, task_id: int) -> int:
        return await self._backend.cancel_task(task_id)

    async def start(self):
        """启动重试服务"""
        self._running = True
        self._worker_task = asyncio.create_task(self._run_loop())
        logger.info("任务重试服务已启动 (Redis ZSet 持久化)")

    async def stop(self):
        """停止重试服务"""
        self._running = False
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass
            self._worker_task = None
        logger.info("任务重试服务已停止")

    async def _run_loop(self):
        """从 Redis ZSet 原子 claim 到期任务并 trigger。

        双 Master 场景下，非 Leader 不能 trigger。
        P1-18: 每轮 sweep_stalled 恢复崩溃遗留 -> claim_due -> trigger -> ack。
        Master 重启 / 切主后 ZSet 数据仍在 Redis,新 Leader 直接接管。
        """
        from antcode_master.leader import ensure_leader

        sweep_every_n = 10
        tick_counter = 0

        while self._running:
            try:
                if not await ensure_leader():
                    # 非 Leader,空转;不消费队列 —— 让 Leader 处理
                    await asyncio.sleep(1.0)
                    continue

                tick_counter += 1
                if tick_counter % sweep_every_n == 1:
                    try:
                        await self._recover_from_db()
                        await self._backend.sweep_stalled()
                    except Exception as exc:
                        logger.warning(f"retry sweep_stalled 异常: {exc}")

                try:
                    claimed = await self._backend.claim_due(limit=32)
                except Exception as exc:
                    logger.error(f"retry claim_due 异常: {exc}")
                    await asyncio.sleep(1)
                    continue

                if not claimed:
                    await asyncio.sleep(1.0)
                    continue

                for item in claimed:
                    task_id = item.get("task_id")
                    new_run_id = await self._handle_claimed_item(item)
                    if new_run_id is not None:
                        logger.info(f"任务 {task_id} 重试已创建: run_id={new_run_id}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"处理重试队列失败: {e}")
                await asyncio.sleep(1)

    async def _handle_claimed_item(self, item: dict[str, Any]) -> str | None:
        """处理单条 claimed intent。

        永久无效的业务 intent 会终结；数据库、Redis、任期等瞬态依赖异常
        只累计诊断次数并重排，绝不清除 PostgreSQL durable intent。
        """
        raw_payload = str(item.get("__raw_payload") or "")
        source_run_id = str(item.get("run_id") or "")
        try:
            return await self._process_claimed_intent(item)
        except RetryTargetInvalidError as exc:
            logger.error(f"retry target 永久不可用,终止 intent: payload={raw_payload} exc={exc}")
            await terminate_durable_intent(source_run_id)
            await self._discard_claimed(raw_payload, source_run_id)
            return None
        except RetryIntentInvalidError as exc:
            logger.error(f"retry intent 永久无效,直接丢弃: payload={raw_payload} exc={exc}")
            await self._discard_claimed(raw_payload, source_run_id)
            return None
        except RetryClaimBusyError as exc:
            # P1-FN-10: 合法 busy 不计 attempts，换 30s 长退避等 task 空闲。
            logger.info(
                f"retry intent target task busy,延后 30s requeue(不计入 poison): "
                f"task_id={item.get('task_id')} exc={exc}"
            )
            await self._backend.requeue(raw_payload, delay_seconds=30)
            return None
        except Exception as exc:
            attempts = await self._incr_claimed_attempts(source_run_id)
            logger.error(
                f"创建 retry run 瞬态失败(第 {attempts} 次),保留 durable intent 并 requeue: "
                f"task_id={item.get('task_id')} exc={exc}"
            )
            await self._backend.requeue(raw_payload, delay_seconds=INFRASTRUCTURE_REQUEUE_DELAY_SECONDS)
            return None

    async def _incr_claimed_attempts(self, source_run_id: str) -> int:
        """按 source run 记录连续基础设施失败次数。"""
        if not source_run_id:
            raise RetryIntentInvalidError("retry payload 缺少 source run_id")
        return await self._backend.incr_attempts(source_run_id)

    async def _discard_claimed(self, raw_payload: str, source_run_id: str = "") -> None:
        """丢弃 poison payload:清 processing hash + attempts 计数,不再回 pending。"""
        try:
            await self._backend.ack(raw_payload)
        except Exception as exc:
            # ack 失败说明 processing entry 已被 sweep 等旁路清走,记录即可,
            # 不让丢弃动作打断本轮 claim 批次。
            logger.warning(f"丢弃 retry payload 时清理 processing 失败: {exc}")
        if source_run_id:
            try:
                await self._backend.clear_attempts(source_run_id)
            except Exception as exc:
                logger.warning(f"丢弃 retry payload 时清理 attempts 计数失败: run_id={source_run_id} exc={exc}")

    async def _process_claimed_intent(self, item: dict[str, Any]) -> str:
        """创建 retry run、清理 durable intent，最后 ACK Redis claim。"""
        from antcode_master.control.scheduler_loop import scheduler_service

        raw_payload = str(item.get("__raw_payload") or "")
        source_run_id = str(item.get("run_id") or "")
        if not raw_payload or not source_run_id:
            raise RetryIntentInvalidError("retry payload 缺少 raw payload 或 source run_id")
        intent = _parse_retry_intent(item, source_run_id)
        new_run_id = await scheduler_service.trigger_retry_intent(intent)
        await self._clear_durable_intent(source_run_id)
        await self._backend.ack(raw_payload)
        return new_run_id

    async def _clear_durable_intent(self, source_run_id: str) -> None:
        # P1-FN-05: 权威清除已并入 Master 创建新 run 的同一事务
        # (scheduler_loop._consume_retry_intent);此处仅为提交后校验 + 兜底补清。
        cleared = await TaskRun.filter(run_id=source_run_id, next_retry_at__not_isnull=True).update(next_retry_at=None)
        if cleared != 1:
            source = await TaskRun.get_or_none(run_id=source_run_id)
            if source is not None and source.next_retry_at is not None:
                raise RuntimeError(f"durable retry intent 清理失败: run_id={source_run_id}")
        # 成功消费后清除仅用于诊断的连续失败计数。
        await self._backend.clear_attempts(source_run_id)

    async def _recover_from_db(self) -> int:
        """Rebuild every durable intent using stable keyset pagination."""
        return await recover_retry_intents(self._backend)


retry_service = RetryService()
