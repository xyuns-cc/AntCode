"""
DataService gRPC 服务实现 (P1c)

负责高频数据面：

- ``StreamTasks``：订阅 Redis ready stream 并转码为 ``TaskDispatch``。
- ``AckTask``：worker 收到任务后回执 (accept/reject)，对应 XACK 或重新入队。
- ``StreamStatus`` / ``StreamLogs``：校验后写入统一结果与日志 Stream。

"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import grpc
from antcode_contracts import data_pb2
from antcode_contracts.data_pb2_grpc import DataServiceServicer
from antcode_core.application.services.workers.log_ingest_fence import LogIngestFenceRejected
from antcode_core.application.services.workers.run_ownership_service import (
    require_worker_owns_runs,
    require_worker_owns_runs_for_lease,
    require_worker_owns_spider_run,
)
from loguru import logger

from antcode_gateway.auth import require_authenticated_worker
from antcode_gateway.handlers import (
    LogHandler,
    ResultHandler,
    SpiderDataHandler,
    TaskPollHandler,
)
from antcode_gateway.handlers.poll import task_info_to_dispatch
from antcode_gateway.services.stream_frame_guards import require_bounded_status_frame, require_owned_receipt

# StreamTasks 内部从 ready stream 拉取的阻塞超时（毫秒）。
# 选小一点避免 server-stream 退出后还卡在 redis 阻塞读上。
STREAM_TASKS_BLOCK_MS = 2_000


async def _missing_lease_verifier(_worker_id: str, _lease_id: str) -> bool:
    raise RuntimeError("Gateway lease verifier 未配置")


class GatewayDataService(DataServiceServicer):
    """DataService Gateway 端实现。"""

    def __init__(
        self,
        *,
        poll_handler: TaskPollHandler | None = None,
        result_handler: ResultHandler | None = None,
        log_handler: LogHandler | None = None,
        spider_data_handler: SpiderDataHandler | None = None,
        ownership_verifier=require_worker_owns_runs,
        log_ownership_verifier=require_worker_owns_runs_for_lease,
        spider_ownership_verifier=require_worker_owns_spider_run,
        lease_verifier=_missing_lease_verifier,
    ):
        self._poll = poll_handler or TaskPollHandler()
        self._result = result_handler or ResultHandler()
        self._logs = log_handler or LogHandler()
        # T6-T3b: gateway 模式的 rule/spider 数据落地通道
        self._spider_data = spider_data_handler or SpiderDataHandler()
        self._ownership_verifier = ownership_verifier
        self._log_ownership_verifier = log_ownership_verifier
        self._spider_ownership_verifier = spider_ownership_verifier
        self._lease_verifier = lease_verifier
        logger.info("DataService 已初始化")

    async def _require_run_ownership(
        self,
        context: grpc.aio.ServicerContext,
        worker_id: str,
        run_ids: set[str],
    ) -> None:
        try:
            await self._ownership_verifier(worker_id, run_ids)
        except (PermissionError, ValueError) as exc:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except Exception:
            logger.exception("TaskRun ownership 校验失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "TaskRun ownership 校验失败")

    async def _require_spider_ownership(
        self,
        context: grpc.aio.ServicerContext,
        *,
        worker_id: str,
        run_id: str,
        project_id: str,
        lease_id: str,
    ) -> None:
        try:
            if not await self._lease_verifier(worker_id, lease_id):
                raise PermissionError("SpiderData lease_id 不是当前 Worker Lease")
            await self._spider_ownership_verifier(
                worker_id,
                run_id,
                project_id,
                lease_id=lease_id,
            )
        except (PermissionError, ValueError) as exc:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except Exception:
            logger.exception("SpiderData ownership 校验失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "SpiderData ownership 校验失败")

    async def _require_current_lease(self, context, worker_id: str, lease_id: str) -> None:
        if not lease_id:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "worker lease is not current")
        try:
            current = await self._lease_verifier(worker_id, lease_id)
        except grpc.aio.AbortError:
            raise
        except Exception:
            logger.exception("Worker lease 校验失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "worker lease verification unavailable")
        if not current:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "worker lease is not current")

    async def _require_log_ownership(
        self,
        context,
        *,
        worker_id: str,
        lease_id: str,
        run_ids: set[str],
    ) -> None:
        await self._require_current_lease(context, worker_id, lease_id)
        try:
            await self._log_ownership_verifier(worker_id, run_ids, lease_id=lease_id)
        except (PermissionError, ValueError) as exc:
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except grpc.aio.AbortError:
            raise
        except Exception:
            logger.exception("Log TaskRun lease ownership 校验失败")
            await context.abort(grpc.StatusCode.UNAVAILABLE, "Log TaskRun ownership 校验失败")

    # =========================================================================
    # StreamTasks (server-streaming)
    # =========================================================================

    async def StreamTasks(
        self,
        request: data_pb2.SubscribeRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[data_pb2.TaskDispatch]:
        worker_id = await require_authenticated_worker(context, request.worker_id)
        if not worker_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "worker_id 不能为空")
            return

        await self._require_current_lease(context, worker_id, request.lease_id)
        await context.send_initial_metadata(())
        prefetch = request.prefetch if request.prefetch > 0 else 1
        logger.info(f"StreamTasks 建立: worker_id={worker_id} prefetch={prefetch}")
        try:
            while True:
                if context.cancelled():
                    break
                await self._require_current_lease(context, worker_id, request.lease_id)
                try:
                    # P1-GW-01: 以代际 consumer 读取，结算侧按 consumer 归属
                    # 原子校验（见 poll.generation_consumer）。
                    tasks = await self._poll.handle(
                        worker_id=worker_id,
                        lease_id=request.lease_id,
                        max_tasks=prefetch,
                        block_ms=STREAM_TASKS_BLOCK_MS,
                    )
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.exception(f"StreamTasks poll 异常: {exc}")
                    await asyncio.sleep(1.0)
                    continue

                if not tasks:
                    continue

                # 阻塞读取后复检 Lease；失败时保留 PEL 给新代际接管。
                await self._require_current_lease(context, worker_id, request.lease_id)
                for task in tasks:
                    await self._require_current_lease(context, worker_id, request.lease_id)
                    dispatch = task_info_to_dispatch(task)
                    yield dispatch

        except asyncio.CancelledError:
            logger.info(f"StreamTasks 被取消: worker_id={worker_id}")
            raise
        finally:
            logger.info(f"StreamTasks 已断开: worker_id={worker_id}")

    # AckTask
    # =========================================================================

    async def AckTask(
        self,
        request: data_pb2.AckTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> data_pb2.AckTaskResponse:
        worker_id = await require_authenticated_worker(context, request.worker_id)
        # P1-GW-01: 结算（ACK/requeue）也必须持有当前代际 Lease，旧代际
        # 进程不得再结算新代际拥有的任务。
        await self._require_current_lease(context, worker_id, request.lease_id)
        receipt_id = request.receipt_id or ""
        if not await require_owned_receipt(context, worker_id, receipt_id):  # P2-#19
            return data_pb2.AckTaskResponse(success=False, error="receipt 校验失败")
        try:
            outcome = await self._poll.ack_receipt(
                receipt_id=receipt_id,
                accepted=bool(request.accepted),
                reason=request.reason or "",
                worker_id=worker_id,
                lease_id=request.lease_id,
            )
        except Exception as exc:
            logger.exception(f"AckTask 异常: {exc}")
            # 底层异常归为不可用,触发 worker retry
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
            return data_pb2.AckTaskResponse(success=False, error=str(exc))
        if outcome == self._poll.ACK_OUTCOME_NOT_OWNER:
            # P1-GW-01: entry 已被新代际接管，旧代际结算被 Lua 原子拒绝。
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "task receipt owned by another lease generation",
            )
            return data_pb2.AckTaskResponse(success=False, error="not owner")
        success = self._poll.is_settle_success(outcome)
        return data_pb2.AckTaskResponse(
            success=success,
            error="" if success else "ack failed",
        )

    # =========================================================================
    # StreamStatus (client-streaming)
    # =========================================================================

    async def StreamStatus(
        self,
        request_iterator: AsyncIterator[data_pb2.TaskStatus],
        context: grpc.aio.ServicerContext,
    ) -> data_pb2.StatusAck:
        # P1-#7: 每条 status 用独立 try/except 包裹, 失败的累计 cnt 上报。
        # 落 stream 失败时 abort(UNAVAILABLE), 让 worker 端重试,绝不静默吞掉。
        received = 0
        failed = 0
        try:
            async for task_status in request_iterator:
                if not await require_bounded_status_frame(context, task_status):  # P1-SEC-04
                    return data_pb2.StatusAck(received=received)
                worker_id = await require_authenticated_worker(context, task_status.worker_id)
                # TaskStatus 无顶层 lease_id，沿用 data map 的代际字段。
                status_lease_id = str(task_status.data.get("lease_id", "") or "").strip()
                await self._require_current_lease(context, worker_id, status_lease_id)
                await self._require_run_ownership(context, worker_id, {task_status.run_id})
                try:
                    ok = await self._result.handle(task_status)
                except Exception as exc:
                    logger.exception(f"StreamStatus.handle 异常: run_id={task_status.run_id} exc={exc}")
                    failed += 1
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        f"status stream write failed: {exc}",
                    )
                    return data_pb2.StatusAck(received=received)
                if ok:
                    received += 1
                else:
                    failed += 1
                    logger.warning(f"StreamStatus 落 stream 失败: run_id={task_status.run_id}")
                    # ack 写入失败 -> 让 worker 重试。
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        "status stream write failed",
                    )
                    return data_pb2.StatusAck(received=received)
        except asyncio.CancelledError:
            logger.info(f"StreamStatus 被取消，已 received={received} failed={failed}")
            raise
        except grpc.aio.AbortError:
            raise
        except Exception as exc:
            logger.exception(f"StreamStatus 异常: {exc}")
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
        return data_pb2.StatusAck(received=received)

    # =========================================================================
    # StreamLogs (client-streaming)
    # =========================================================================

    async def StreamLogs(
        self,
        request_iterator: AsyncIterator[data_pb2.LogBatch],
        context: grpc.aio.ServicerContext,
    ) -> data_pb2.LogAck:
        # P1-#7: 每个 batch 用独立 try/except 包裹, 失败的累计 cnt 上报。
        # 落 stream 失败时 abort(UNAVAILABLE) 让 worker 重试, 绝不静默吞掉。
        received = 0
        failed = 0
        try:
            async for batch in request_iterator:
                worker_id = await require_authenticated_worker(context, batch.worker_id)
                await self._require_log_ownership(
                    context,
                    worker_id=worker_id,
                    lease_id=batch.lease_id,
                    run_ids={entry.run_id for entry in batch.entries},
                )
                try:
                    ok = await self._logs.handle_log_batch(batch)
                except LogIngestFenceRejected as exc:
                    failed += 1
                    await context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        f"log generation superseded: {exc.reason}",
                    )
                    return data_pb2.LogAck(received=received)
                except ValueError as exc:
                    logger.warning(f"StreamLogs 输入无效: worker_id={batch.worker_id} exc={exc}")
                    failed += 1
                    await context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        f"invalid log batch: {exc}",
                    )
                    return data_pb2.LogAck(received=received)
                except Exception as exc:
                    logger.exception(f"StreamLogs.handle_log_batch 异常: worker_id={batch.worker_id} exc={exc}")
                    failed += 1
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        f"log stream write failed: {exc}",
                    )
                    return data_pb2.LogAck(received=received)
                if ok:
                    received += len(batch.entries)
                else:
                    failed += 1
                    logger.warning(f"StreamLogs 写 Stream 失败: worker_id={batch.worker_id}")
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        "log stream write failed",
                    )
                    return data_pb2.LogAck(received=received)
        except asyncio.CancelledError:
            logger.info(f"StreamLogs 被取消，已 received={received} failed={failed}")
            raise
        except grpc.aio.AbortError:
            raise
        except Exception as exc:
            logger.exception(f"StreamLogs 异常: {exc}")
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
        return data_pb2.LogAck(received=received)

    # =========================================================================
    # T6-T3b: StreamSpiderData (client-streaming)
    # =========================================================================

    async def StreamSpiderData(
        self,
        request_iterator: AsyncIterator[data_pb2.SpiderDataBatch],
        context: grpc.aio.ServicerContext,
    ) -> data_pb2.SpiderDataAck:
        """gateway 模式下 worker 上报 Scrapy pipeline 抓到的 item。

        字段与 direct 模式 xadd 一一对齐，web_api 侧读取逻辑不用改。
        任何 batch 失败 → abort，让 worker 端重连 gRPC 流重试。
        """
        total_accepted = 0
        total_failed = 0
        try:
            async for batch in request_iterator:
                worker_id = await require_authenticated_worker(context, batch.worker_id)
                await self._require_canonical_spider_ids(context, batch)
                await self._require_spider_ownership(
                    context,
                    worker_id=worker_id,
                    run_id=batch.run_id,
                    project_id=batch.project_id,
                    lease_id=batch.lease_id,
                )
                try:
                    accepted, failed = await self._spider_data.handle_batch(batch)
                except ValueError as exc:
                    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
                    return data_pb2.SpiderDataAck(accepted=total_accepted, failed=total_failed)
                except Exception as exc:
                    logger.exception(
                        f"StreamSpiderData.handle_batch 异常: "
                        f"worker_id={batch.worker_id} run_id={batch.run_id} exc={exc}"
                    )
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        f"spider data write failed: {exc}",
                    )
                    return data_pb2.SpiderDataAck(accepted=total_accepted, failed=total_failed)
                total_accepted += accepted
                total_failed += failed
        except asyncio.CancelledError:
            logger.info(f"StreamSpiderData 被取消: accepted={total_accepted} failed={total_failed}")
            raise
        except grpc.aio.AbortError:
            raise
        except Exception as exc:
            logger.exception(f"StreamSpiderData 异常: {exc}")
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
        return data_pb2.SpiderDataAck(accepted=total_accepted, failed=total_failed)

    @staticmethod
    async def _require_canonical_spider_ids(
        context: grpc.aio.ServicerContext,
        batch: data_pb2.SpiderDataBatch,
    ) -> None:
        identifiers = {
            "run_id": batch.run_id,
            "project_id": batch.project_id,
            "lease_id": batch.lease_id,
        }
        invalid = [name for name, value in identifiers.items() if not value or value != value.strip()]
        if invalid:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"SpiderData 标识必须非空且无首尾空白: {', '.join(invalid)}",
            )
