"""Gateway sink —— 走 ``DataService.StreamSpiderData`` gRPC 上报。

不直连 Redis，跨网 worker 的唯一 spider data 路径。字段严格对齐 direct
模式的 xadd payload，gateway 侧再原样翻译成 xadd。

**批处理策略**：
- write_item 累积到 batch buffer；每 ``_batch_size`` 条或每 ``_flush_interval_s``
  秒 flush 一次
- close 时最后 flush 一次剩余的
- 心跳 meta 附在下一次 flush 的 batch 里，不额外发 batch

**重连**：gRPC 流断连时 batch 缓冲丢失（跟 direct 模式 Redis 断了失败 xadd
一个语义级别），依赖 pipeline 侧的 XADD_FAIL_THRESHOLD 兜底走 CloseSpider。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from loguru import logger


class GatewaySpiderDataSink:
    """gRPC client-streaming sink。"""

    DEFAULT_BATCH_SIZE = 50
    DEFAULT_FLUSH_INTERVAL_S = 2.0

    def __init__(self, *, endpoint: str, secure: bool = False, token: str = "") -> None:
        self._endpoint = endpoint
        self._secure = secure
        self._token = token
        self._channel: Any = None
        self._stub: Any = None
        self._worker_id = os.environ.get("ANTCODE_WORKER_ID", "").strip() or "unknown"
        self._run_id = ""
        self._project_id = ""
        self._spider_name = ""
        self._batch_items: list[Any] = []
        self._pending_meta: dict[str, str] = {}
        self._batch_size = int(os.environ.get("ANTCODE_SPIDER_GATEWAY_BATCH_SIZE", "") or self.DEFAULT_BATCH_SIZE)
        self._flush_interval_s = float(
            os.environ.get("ANTCODE_SPIDER_GATEWAY_FLUSH_INTERVAL", "") or self.DEFAULT_FLUSH_INTERVAL_S
        )
        self._last_flush = 0.0
        self._lock = asyncio.Lock()
        # P2-02: 后台定时 flush 任务
        self._flush_task: asyncio.Task | None = None
        self._closing = False
        self._unreported_written = 0

    async def open(
        self,
        *,
        run_id: str,
        project_id: str,
        spider_name: str,
        namespace: str,
    ) -> None:
        import grpc  # 延迟导入
        from antcode_contracts import data_pb2_grpc

        self._run_id = run_id
        self._project_id = project_id
        self._spider_name = spider_name

        if self._secure:
            creds = grpc.ssl_channel_credentials()
            self._channel = grpc.aio.secure_channel(self._endpoint, creds)
        else:
            self._channel = grpc.aio.insecure_channel(self._endpoint)
        self._stub = data_pb2_grpc.DataServiceStub(self._channel)
        logger.info(f"GatewaySpiderDataSink 就绪: endpoint={self._endpoint} secure={self._secure} run_id={run_id}")
        self._last_flush = asyncio.get_event_loop().time()

        # P2-02: 起后台定时 flush 任务。老实现只在 write_item 时按
        # ``now - _last_flush >= flush_interval`` 触发 flush，慢速 spider
        # 抓完最后 <batch_size 条后长时间空转，buffer 数据就滞留在内存里。
        # 用独立 task 保证即使无新 item 进来也能按周期把 buffer 排出去。
        self._closing = False
        self._flush_task = asyncio.create_task(self._periodic_flush())
        # 挂个 done_callback 吃掉未 await 的异常，避免 "Task exception was
        # never retrieved" 噪音（真业务失败在 _flush 内部已 log）。
        self._flush_task.add_done_callback(self._on_flush_task_done)

    def _build_item(
        self,
        *,
        item_id: str,
        item_type: str,
        data_json: str,
        url: str,
        timestamp: str,
        sequence: int,
    ):
        from antcode_contracts import data_pb2

        return data_pb2.SpiderDataItem(
            item_id=item_id,
            spider_name=self._spider_name,
            item_type=item_type or "default",
            data=data_json.encode("utf-8"),
            url=url,
            timestamp=timestamp,
            sequence=sequence,
        )

    async def write_item(
        self,
        *,
        item_id: str,
        item_type: str,
        data_json: str,
        url: str,
        timestamp: str,
        sequence: int,
    ) -> tuple[bool, int]:
        """把 item 塞进 buffer；到阈值就 flush。

        Returns:
            ``(ok, n_written)``：
            - 只是入 buffer 未 flush → ``(True, 0)``
            - 触发 flush 且 gateway 全部 ack → ``(True, n_flushed)``
            - 触发 flush 但 RPC/ack 失败 → ``(False, 0)``（buffer 已恢复）
        """
        item = self._build_item(
            item_id=item_id,
            item_type=item_type,
            data_json=data_json,
            url=url,
            timestamp=timestamp,
            sequence=sequence,
        )
        async with self._lock:
            self._batch_items.append(item)
            now = asyncio.get_event_loop().time()
            should_flush = (
                len(self._batch_items) >= self._batch_size or (now - self._last_flush) >= self._flush_interval_s
            )
        if should_flush:
            ok, _ = await self._flush()
            if not ok:
                return False, 0
            return True, await self.consume_written_count()
        return True, 0

    async def consume_written_count(self) -> int:
        """返回后台或前台 flush 已确认但尚未计入 Scrapy stats 的条数。"""
        async with self._lock:
            written = self._unreported_written
            self._unreported_written = 0
            return written

    async def write_meta(self, fields: dict[str, str]) -> None:
        async with self._lock:
            self._pending_meta.update(fields)

    async def _flush(self) -> tuple[bool, int]:
        """把 buffer 里的 items + pending meta 组一个 SpiderDataBatch 发出去。

        **P1-27 修复**：先复制 buffer 到本地 ``pending`` 再清空，实际发送在锁外；
        发送失败（RPC 异常或 ack.failed > 0）**必须把 items / meta 恢复回 buffer**，
        否则数据就永久丢了，pipeline 那侧还会误以为写成功。

        Returns:
            ``(ok, n_written)``：
            - buffer 本来就是空 → ``(True, 0)``
            - RPC 成功且 ack.failed=0 → ``(True, len(items))``
            - 任一失败 → ``(False, 0)``（buffer/meta 已合并回队首）
        """
        pending_items, pending_meta = await self._take_pending()
        if not pending_items and not pending_meta:
            return True, 0
        batch = self._build_batch(pending_items, pending_meta)
        return await self._send_pending(batch, pending_items, pending_meta)

    async def _take_pending(self) -> tuple[list[Any], dict[str, str]]:
        async with self._lock:
            pending_items = list(self._batch_items)
            pending_meta = dict(self._pending_meta)
            self._batch_items.clear()
            self._pending_meta.clear()
            self._last_flush = asyncio.get_event_loop().time()
            return pending_items, pending_meta

    def _build_batch(self, pending_items: list[Any], pending_meta: dict[str, str]):
        from antcode_contracts import data_pb2

        batch = data_pb2.SpiderDataBatch(
            worker_id=self._worker_id,
            run_id=self._run_id,
            project_id=self._project_id,
        )
        batch.items.extend(pending_items)
        if pending_meta:
            batch.meta.CopyFrom(self._build_meta(pending_meta))
        return batch

    @staticmethod
    def _build_meta(pending_meta: dict[str, str]):
        from antcode_contracts import data_pb2

        meta = data_pb2.SpiderMetaUpdate()
        meta.status = pending_meta.get("status", "")
        items_count = pending_meta.get("items_count")
        if items_count is not None:
            meta.items_count = int(items_count)
        meta.last_item_at = pending_meta.get("last_item_at", "")
        return meta

    async def _send_pending(
        self,
        batch: Any,
        pending_items: list[Any],
        pending_meta: dict[str, str],
    ) -> tuple[bool, int]:
        try:
            ack = await self._send_batch(batch)
        except Exception as exc:
            logger.error(f"gateway StreamSpiderData 失败: {exc}；恢复 {len(pending_items)} 条待重试")
            await self._restore_pending(pending_items, pending_meta)
            return False, 0
        if getattr(ack, "failed", 0):
            logger.warning(
                f"gateway StreamSpiderData 部分失败: accepted={getattr(ack, 'accepted', 0)} "
                f"failed={ack.failed}；恢复 {len(pending_items)} 条待重试"
            )
            await self._restore_pending(pending_items, pending_meta)
            return False, 0
        async with self._lock:
            self._unreported_written += len(pending_items)
        return True, len(pending_items)

    async def _send_batch(self, batch: Any) -> Any:
        if self._stub is None:
            raise RuntimeError("Gateway sink 尚未 open")

        async def _iter():
            yield batch

        metadata = []
        if self._token:
            metadata.append(("authorization", f"Bearer {self._token}"))
        return await self._stub.StreamSpiderData(_iter(), metadata=metadata)

    @staticmethod
    def _on_flush_task_done(task: asyncio.Task) -> None:
        """吃掉后台 task 未处理的异常，避免 asyncio warning。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"gateway 后台 flush task 异常退出: {exc}")

    async def _periodic_flush(self) -> None:
        """P2-02: 后台按 ``_flush_interval_s`` 周期把 buffer 排出去。

        - buffer 空 → 跳过（不发空 batch，不额外 RPC）
        - buffer 非空 → 调 ``_flush()`` 走正常路径（含失败恢复）
        - close() 会置 ``_closing=True`` 并 cancel 本 task，正常退出
        """
        while not self._closing:
            try:
                await asyncio.sleep(self._flush_interval_s)
            except asyncio.CancelledError:
                break
            if self._closing:
                break
            try:
                async with self._lock:
                    has_data = bool(self._batch_items) or bool(self._pending_meta)
                if has_data:
                    await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pragma: no cover
                # _flush 内部已经处理失败恢复；这里兜底防止 task 意外崩掉
                logger.warning(f"gateway 周期 flush 失败: {exc}")

    async def _restore_pending(
        self,
        pending_items: list[Any],
        pending_meta: dict[str, str],
    ) -> None:
        """flush 失败时把 items / meta 合并回 buffer 头部。

        - items：pending 排在新进来的前面，保原始 sequence 顺序
        - meta：pending 值先落入，被期间累积的新值覆盖（新覆盖旧）
        """
        async with self._lock:
            if pending_items:
                self._batch_items = pending_items + self._batch_items
            if pending_meta:
                merged = dict(pending_meta)
                merged.update(self._pending_meta)
                self._pending_meta = merged

    async def close(self, final_meta: dict[str, str] | None = None) -> tuple[bool, int]:
        """把剩余的 items + final_meta 一起 flush 出去，然后关 channel。

        **P1-27 修复**：调用方必须知道 close 是否真的把 buffer 排干净；旧版
        丢弃 ``_flush()`` 结果导致 pipeline 把 "还有 N 条没送出去" 当成成功。

        **P2-02 修复**：先把后台 ``_periodic_flush`` task cancel 掉再做最终
        flush；否则后台 task 与 close() 会并发抢 ``_flush()``，还可能在
        channel 关闭后继续尝试发送导致 "Cannot invoke RPC on closed channel"
        噪音日志。cancel 后 await 一次让它干净退出。

        Returns:
            ``(ok, remaining)``：
            - ok=True 且 remaining=0：全部落地成功
            - ok=False 或 remaining>0：有数据未成功送达，pipeline/CLI 需以失败计
        """
        # P2-02: 先停后台 flush，避免与本次 final flush 抢锁 / channel
        self._closing = True
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                # 已挂 done_callback 兜异常；这里等 task 落地即可
                pass
        self._flush_task = None

        if final_meta:
            await self.write_meta(final_meta)
        # flush 掉最后剩余的
        flush_ok, _ = await self._flush()
        async with self._lock:
            remaining = len(self._batch_items)
        if self._channel is not None:
            try:
                await self._channel.close()
            except Exception:
                pass
        return flush_ok, remaining
