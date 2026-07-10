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

    def __init__(
        self, *, endpoint: str, secure: bool = False, token: str = ""
    ) -> None:
        self._endpoint = endpoint
        self._secure = secure
        self._token = token
        self._channel = None
        self._stub = None
        self._worker_id = os.environ.get("ANTCODE_WORKER_ID", "").strip() or "unknown"
        self._run_id = ""
        self._project_id = ""
        self._spider_name = ""
        self._batch_items: list[Any] = []
        self._pending_meta: dict[str, str] = {}
        self._batch_size = int(
            os.environ.get("ANTCODE_SPIDER_GATEWAY_BATCH_SIZE", "")
            or self.DEFAULT_BATCH_SIZE
        )
        self._flush_interval_s = float(
            os.environ.get("ANTCODE_SPIDER_GATEWAY_FLUSH_INTERVAL", "")
            or self.DEFAULT_FLUSH_INTERVAL_S
        )
        self._last_flush = 0.0
        self._lock = asyncio.Lock()

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
        logger.info(
            f"GatewaySpiderDataSink 就绪: endpoint={self._endpoint} "
            f"secure={self._secure} run_id={run_id}"
        )
        self._last_flush = asyncio.get_event_loop().time()

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
                len(self._batch_items) >= self._batch_size
                or (now - self._last_flush) >= self._flush_interval_s
            )
        if should_flush:
            return await self._flush()
        return True, 0

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
        from antcode_contracts import data_pb2

        async with self._lock:
            if not self._batch_items and not self._pending_meta:
                return True, 0
            pending_items = list(self._batch_items)
            pending_meta = dict(self._pending_meta)
            self._batch_items.clear()
            self._pending_meta.clear()
            self._last_flush = asyncio.get_event_loop().time()

        batch = data_pb2.SpiderDataBatch(
            worker_id=self._worker_id,
            run_id=self._run_id,
            project_id=self._project_id,
        )
        for item in pending_items:
            batch.items.append(item)
        if pending_meta:
            meta = data_pb2.SpiderMetaUpdate()
            if "status" in pending_meta:
                meta.status = pending_meta["status"]
            if "items_count" in pending_meta:
                try:
                    meta.items_count = int(pending_meta["items_count"])
                except (TypeError, ValueError):
                    pass
            if "last_item_at" in pending_meta:
                meta.last_item_at = pending_meta["last_item_at"]
            batch.meta.CopyFrom(meta)

        # 单 batch 一条 client-streaming 消息即可完成往返（gateway 的
        # StreamSpiderData 是 client-streaming，一次 request → 一个 ack）。
        # 用一个 one-shot 的 async generator 送这一个 batch。
        async def _iter():
            yield batch

        metadata = []
        if self._token:
            metadata.append(("authorization", f"Bearer {self._token}"))
        try:
            ack = await self._stub.StreamSpiderData(_iter(), metadata=metadata)
            failed = getattr(ack, "failed", 0)
            if failed:
                accepted = getattr(ack, "accepted", 0)
                logger.warning(
                    f"gateway StreamSpiderData 部分失败: accepted={accepted} "
                    f"failed={failed}；恢复 {len(pending_items)} 条待重试"
                )
                await self._restore_pending(pending_items, pending_meta)
                return False, 0
            return True, len(pending_items)
        except Exception as exc:
            logger.error(
                f"gateway StreamSpiderData 失败: {exc}；恢复 "
                f"{len(pending_items)} 条待重试"
            )
            await self._restore_pending(pending_items, pending_meta)
            return False, 0

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

    async def close(
        self, final_meta: dict[str, str] | None = None
    ) -> tuple[bool, int]:
        """把剩余的 items + final_meta 一起 flush 出去，然后关 channel。

        **P1-27 修复**：调用方必须知道 close 是否真的把 buffer 排干净；旧版
        丢弃 ``_flush()`` 结果导致 pipeline 把 "还有 N 条没送出去" 当成成功。

        Returns:
            ``(ok, remaining)``：
            - ok=True 且 remaining=0：全部落地成功
            - ok=False 或 remaining>0：有数据未成功送达，pipeline/CLI 需以失败计
        """
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
