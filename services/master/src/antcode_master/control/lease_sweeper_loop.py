"""Lease Sweeper Loop — Master 端定时剔除过期 Worker (P3)

每秒扫描 ``LeaseStore`` 中已过期的 lease。对每个被剔除的 Worker：

1. 调用注册的 ``on_worker_evicted`` 回调，让上层（任务回收 / DB 状态翻转）
   有机会执行清理（默认实现只写一条 audit 事件，可由外部覆盖）。
2. 写一条 ``worker_evicted`` 事件到 ``audit:security`` Stream，保持与
   AuthInterceptor 的安全审计风格一致 — 字段 schema 完全相同
   （event_type / worker_id / peer / reason / ts）。

放在 ``loops/`` 下而不是 ``control/`` 下：P4 还会重组 master 的目录布局，
这里先与现有 reconcile_loop / scheduler_loop 同层，避免和 P4 抢路径。

Validates: Requirements (P3 设计文档 §LeaseSweeper)
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from antcode_core.application.services.lease_service import LeaseStore
    from antcode_core.infrastructure.redis.stream_client import StreamClient


# 与 antcode_gateway.auth.AUDIT_SECURITY_STREAM 同名 / 同 maxlen，保证一致风格。
AUDIT_SECURITY_STREAM = "audit:security"
AUDIT_SECURITY_MAXLEN = 100_000


EvictionCallback = Callable[[str], Awaitable[None]]


class LeaseSweeperLoop:
    """每 ``interval`` 秒扫描一次 LeaseStore，剔除离线 Worker。

    Args:
        lease_store: ``LeaseStore`` 实例（已绑定 redis_client + namespace）。
        on_worker_evicted: 单 worker 被剔除后的异步回调。如果为 None，
            内部只会写 audit 事件（不会触发任务回收）。
        audit_stream: 可选 ``StreamClient``；为 None 时跳过审计写入。
        interval: 扫描间隔（秒），默认 1s。
        batch: 每轮 sweep 的最大 worker 数（防单轮过长阻塞 loop）。
    """

    DEFAULT_INTERVAL = 1.0

    def __init__(
        self,
        lease_store: LeaseStore,
        on_worker_evicted: EvictionCallback | None = None,
        audit_stream: StreamClient | None = None,
        interval: float = DEFAULT_INTERVAL,
        batch: int = 100,
    ):
        self._lease_store = lease_store
        self._on_worker_evicted = on_worker_evicted
        self._audit_stream = audit_stream
        self._interval = max(0.1, float(interval))
        self._batch = max(1, int(batch))
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """启动后台 sweep 协程。"""
        if self._running:
            logger.warning("LeaseSweeperLoop 已在运行")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "LeaseSweeperLoop 已启动: interval={}s batch={} namespace={}",
            self._interval,
            self._batch,
            self._lease_store.namespace,
        )

    async def stop(self) -> None:
        """停止 sweep 协程并等待回收。"""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("LeaseSweeperLoop 已停止")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                evicted = await self._lease_store.sweep_expired(batch=self._batch)
                if evicted:
                    await self._handle_evictions(evicted)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("LeaseSweeperLoop sweep 异常")

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def _handle_evictions(self, worker_ids: list[str]) -> None:
        """对每个被剔除的 Worker 触发回调 + 写 audit。

        P2-#38：原实现串行 ``for worker_id``，一轮 sweep 在 worker 数
        多时退化为 O(N) × 任务回收耗时，远超 sweep ``interval``。改为
        ``asyncio.gather`` 并发；逐项检查 Exception 不向上抛，保证 audit
        阶段对每个 worker 都能写一条事件。
        """
        if not worker_ids:
            return

        # 1) 并发回调 — 允许调用方做任务回收等重活。
        if self._on_worker_evicted is not None:
            results = await asyncio.gather(
                *(self._on_worker_evicted(w) for w in worker_ids),
                return_exceptions=True,
            )
            for worker_id, result in zip(worker_ids, results, strict=False):
                if isinstance(result, BaseException):
                    logger.opt(exception=result).error(
                        f"on_worker_evicted 回调异常: worker_id={worker_id}"
                    )

        # 2) 并发 audit — 与 gateway 安全审计一致的字段 schema。
        audit_results = await asyncio.gather(
            *(self._emit_audit(w) for w in worker_ids),
            return_exceptions=True,
        )
        for worker_id, result in zip(worker_ids, audit_results, strict=False):
            if isinstance(result, BaseException):
                logger.opt(exception=result).error(
                    f"写 worker_evicted audit 失败: worker_id={worker_id}"
                )

    async def _emit_audit(self, worker_id: str) -> None:
        if self._audit_stream is None:
            return
        try:
            await self._audit_stream.xadd(
                AUDIT_SECURITY_STREAM,
                {
                    "event_type": "worker_evicted",
                    "worker_id": worker_id,
                    "peer": "",
                    "reason": "lease_expired",
                    "ts": str(int(time.time() * 1000)),
                },
                maxlen=AUDIT_SECURITY_MAXLEN,
                approximate=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                f"写 worker_evicted audit 失败: worker_id={worker_id}"
            )


__all__ = [
    "LeaseSweeperLoop",
    "AUDIT_SECURITY_STREAM",
]
