"""
DataService gRPC 服务实现 (P1c)

负责高频数据面：

- ``StreamTasks`` (server-stream)：长连接订阅任务派发；底层从
  Redis ``task:ready:{worker_id}`` Stream ``XREADGROUP`` 后转码为
  ``TaskDispatch`` Proto yield 给 Worker。
- ``AckTask``：worker 收到任务后回执 (accept/reject)，对应 XACK 或重新入队。
- ``StreamStatus`` (client-stream)：worker 把每条 ``TaskStatus`` 推上来，
  Gateway 用 ``ProtoCodec(TaskStatus)`` 落 task_result_stream（单字段 'p'
  框架）由 Master ``ResultLoop`` 解码。
- ``StreamLogs`` (client-stream)：worker 推 ``LogBatch``，Gateway 用
  Proto bytes 单字段框架落 log:{run_id} stream。

所有落 Stream 操作都满足 P1a wire-format 约定：``{PROTO_FIELD: bytes}``。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import grpc
from antcode_contracts import data_pb2
from antcode_contracts.data_pb2_grpc import DataServiceServicer
from loguru import logger

from antcode_gateway.handlers import (
    LogHandler,
    ResultHandler,
    SpiderDataHandler,
    TaskPollHandler,
)
from antcode_gateway.handlers.poll import task_info_to_dispatch

if TYPE_CHECKING:  # pragma: no cover
    pass

# StreamTasks 内部从 ready stream 拉取的阻塞超时（毫秒）。
# 选小一点避免 server-stream 退出后还卡在 redis 阻塞读上。
STREAM_TASKS_BLOCK_MS = 2_000


class GatewayDataService(DataServiceServicer):
    """DataService Gateway 端实现。"""

    def __init__(
        self,
        poll_handler: TaskPollHandler | None = None,
        result_handler: ResultHandler | None = None,
        log_handler: LogHandler | None = None,
        spider_data_handler: SpiderDataHandler | None = None,
    ):
        self._poll = poll_handler or TaskPollHandler()
        self._result = result_handler or ResultHandler()
        self._logs = log_handler or LogHandler()
        # T6-T3b: gateway 模式的 rule/spider 数据落地通道
        self._spider_data = spider_data_handler or SpiderDataHandler()
        logger.info("DataService 已初始化")

    # =========================================================================
    # StreamTasks (server-streaming)
    # =========================================================================

    async def StreamTasks(
        self,
        request: data_pb2.SubscribeRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[data_pb2.TaskDispatch]:
        worker_id = request.worker_id
        if not worker_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "worker_id 不能为空")
            return

        prefetch = request.prefetch if request.prefetch > 0 else 1
        logger.info(
            f"StreamTasks 建立: worker_id={worker_id} prefetch={prefetch}"
        )

        try:
            while True:
                if context.cancelled():
                    break
                try:
                    tasks = await self._poll.handle(
                        worker_id=worker_id,
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

                for task in tasks:
                    dispatch = task_info_to_dispatch(task)
                    yield dispatch

        except asyncio.CancelledError:
            logger.info(f"StreamTasks 被取消: worker_id={worker_id}")
            raise
        finally:
            logger.info(f"StreamTasks 已断开: worker_id={worker_id}")

    # =========================================================================
    # AckTask
    # =========================================================================

    async def AckTask(
        self,
        request: data_pb2.AckTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> data_pb2.AckTaskResponse:
        receipt_id = request.receipt_id or ""
        # P2-#19: 协议违规走 gRPC error, 业务失败保留 response 字段
        if not receipt_id:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "receipt_id 缺失",
            )
            return data_pb2.AckTaskResponse(success=False, error="receipt_id 缺失")
        try:
            success = await self._poll.ack_receipt(
                receipt_id=receipt_id,
                accepted=bool(request.accepted),
                reason=request.reason or "",
            )
            return data_pb2.AckTaskResponse(
                success=success,
                error="" if success else "ack failed",
            )
        except Exception as exc:
            logger.exception(f"AckTask 异常: {exc}")
            # 底层异常归为不可用,触发 worker retry
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
            return data_pb2.AckTaskResponse(success=False, error=str(exc))

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
                try:
                    ok = await self._result.handle(task_status)
                except Exception as exc:
                    logger.exception(
                        f"StreamStatus.handle 异常: run_id={task_status.run_id} exc={exc}"
                    )
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
                    logger.warning(
                        f"StreamStatus 落 stream 失败: run_id={task_status.run_id}"
                    )
                    # ack 写入失败 -> 让 worker 重试。
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        "status stream write failed",
                    )
                    return data_pb2.StatusAck(received=received)
        except asyncio.CancelledError:
            logger.info(
                f"StreamStatus 被取消，已 received={received} failed={failed}"
            )
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
                try:
                    ok = await self._logs.handle_log_batch(batch)
                except Exception as exc:
                    logger.exception(
                        f"StreamLogs.handle_log_batch 异常: worker_id={batch.worker_id} exc={exc}"
                    )
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
                    logger.warning(
                        f"StreamLogs 写 Stream 失败: worker_id={batch.worker_id}"
                    )
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        "log stream write failed",
                    )
                    return data_pb2.LogAck(received=received)
        except asyncio.CancelledError:
            logger.info(
                f"StreamLogs 被取消，已 received={received} failed={failed}"
            )
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
                try:
                    accepted, failed = await self._spider_data.handle_batch(batch)
                except Exception as exc:
                    logger.exception(
                        f"StreamSpiderData.handle_batch 异常: "
                        f"worker_id={batch.worker_id} run_id={batch.run_id} exc={exc}"
                    )
                    await context.abort(
                        grpc.StatusCode.UNAVAILABLE,
                        f"spider data write failed: {exc}",
                    )
                    return data_pb2.SpiderDataAck(
                        accepted=total_accepted, failed=total_failed
                    )
                total_accepted += accepted
                total_failed += failed
        except asyncio.CancelledError:
            logger.info(
                f"StreamSpiderData 被取消: accepted={total_accepted} "
                f"failed={total_failed}"
            )
            raise
        except grpc.aio.AbortError:
            raise
        except Exception as exc:
            logger.exception(f"StreamSpiderData 异常: {exc}")
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
        return data_pb2.SpiderDataAck(
            accepted=total_accepted, failed=total_failed
        )
