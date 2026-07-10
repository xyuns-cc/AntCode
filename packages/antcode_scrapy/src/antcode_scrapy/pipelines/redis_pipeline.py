"""AntCode Redis Pipeline —— items → ``spider:data:{run_id}`` stream。

**跨包契约（不可改动）**：
- stream key = ``{namespace}:spider:data:{run_id}``
- 字段名/类型必须与 ``antcode_worker.plugins.spider.data.models
  .SpiderDataItem.to_redis_dict()`` 逐字节一致，否则 web_api
  ``routes/v1/runs.py`` 里的 ``SpiderDataItem.from_redis_dict()`` 会解析失败
- 元数据 key = ``{namespace}:spider:meta:{run_id}`` （Hash）
- 索引 key = ``{namespace}:spider:index:{project_id}`` （ZSet，score=timestamp）

**T6-T3b 拆分**：具体落地逻辑挪到 ``antcode_scrapy.sinks``：
- ``RedisSpiderDataSink`` = direct 模式，走 Redis 直连（原逻辑）
- ``GatewaySpiderDataSink`` = gateway 模式，走 ``DataService.StreamSpiderData``
  gRPC 上报，gateway 转 Redis

pipeline 只做"每条 item 组 payload → sink.write_item"和心跳/去重/终态 meta
的通用逻辑；sink 选择由 env ``ANTCODE_SPIDER_SINK_MODE`` 决定。

设计要点：
- Scrapy 2.13+ 允许 pipeline 的 open_spider/process_item/close_spider 为
  ``async def``，sink 全部 async 接口
- sink 缺配置时（比如 gateway 模式但没设 endpoint）pipeline 静默禁用
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

from loguru import logger


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """读取正整数 env；解析失败或低于下限退回默认值。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return val if val >= minimum else default


class AntCodeRedisPipeline:
    """把 Scrapy 抽出的 dict item 转成结构化数据落地（Redis 直连 or Gateway 上报）。"""

    # R1-P1-9 (审查报告)：连续写失败超过此阈值 → CloseSpider
    DEFAULT_XADD_FAIL_THRESHOLD = 20
    # R5-P2-14: 每 N 条 item 刷一次 meta 心跳
    DEFAULT_HEARTBEAT_INTERVAL = 50

    def __init__(self) -> None:
        self._sink = None
        self._namespace: str = "antcode"
        self._run_id: str = ""
        self._project_id: str = ""
        self._spider_name: str = ""
        self._sequence: int = 0
        self._enabled: bool = False
        self._started_at: datetime | None = None
        self._xadd_fail_count: int = 0
        self._xadd_fail_threshold: int = self.DEFAULT_XADD_FAIL_THRESHOLD
        self._heartbeat_interval: int = self.DEFAULT_HEARTBEAT_INTERVAL

    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    async def open_spider(self, spider) -> None:
        """T6-T3b: 走 sink 抽象。ANTCODE_SPIDER_SINK_MODE 决定 direct/gateway。"""
        try:
            from antcode_scrapy.sinks import create_sink
        except ImportError as exc:  # pragma: no cover
            logger.warning(f"antcode_scrapy.sinks 不可用，pipeline 禁用: {exc}")
            return

        sink = create_sink()
        if sink is None:
            logger.info(
                "spider data sink 未配置 (ANTCODE_SPIDER_SINK_MODE / SINK_URL / "
                "GATEWAY_ENDPOINT)，pipeline 静默禁用"
            )
            return

        self._sink = sink
        self._enabled = True
        self._namespace = (
            os.environ.get("ANTCODE_SPIDER_REDIS_NAMESPACE", "").strip() or "antcode"
        )
        self._run_id = getattr(spider, "run_id", "") or ""
        self._project_id = getattr(spider, "project_id", "") or ""
        self._spider_name = spider.name or "antcode_rule"
        self._sequence = 0
        self._started_at = datetime.now()

        self._xadd_fail_threshold = _env_int(
            "ANTCODE_SPIDER_XADD_FAIL_THRESHOLD", self.DEFAULT_XADD_FAIL_THRESHOLD
        )
        self._heartbeat_interval = _env_int(
            "ANTCODE_SPIDER_HEARTBEAT_INTERVAL", self.DEFAULT_HEARTBEAT_INTERVAL
        )

        if not self._run_id:
            logger.warning("spider.run_id 为空，落地时会用 empty key")

        await self._sink.open(
            run_id=self._run_id,
            project_id=self._project_id,
            spider_name=self._spider_name,
            namespace=self._namespace,
        )
        # 初始 meta
        await self._sink.write_meta(
            {
                "status": "running",
                "started_at": self._started_at.isoformat(),
                "items_count": "0",
            }
        )
        logger.info(
            f"AntCodeRedisPipeline 就绪: sink={type(self._sink).__name__} "
            f"run_id={self._run_id} project={self._project_id} "
            f"heartbeat_every={self._heartbeat_interval}"
        )

    @staticmethod
    def _normalize_write_result(result: Any) -> tuple[bool, int]:
        """兼容 sink.write_item 返回 ``bool`` 或 ``(ok, n_written)``。

        - RedisSpiderDataSink (direct 模式) 每条一次 xadd → 老式 bool
          语义: True 表示这一条落地，n_written=1；False 表示这条丢，n_written=0
        - GatewaySpiderDataSink 走 batch flush → 新式 ``(ok, n_written)``
          语义: n_written 是本次 flush 真正 ack 的条数（未 flush 时为 0）
        """
        if isinstance(result, tuple) and len(result) == 2:
            ok, n = result
            return bool(ok), int(n)
        ok = bool(result)
        return ok, 1 if ok else 0

    @staticmethod
    def _normalize_close_result(result: Any) -> tuple[bool, int]:
        """兼容 sink.close 返回 ``None`` 或 ``(ok, remaining)``。

        - 老式返回 None：视为成功、无剩余（保持 direct 模式向后兼容）
        - 新式返回 ``(ok, remaining)``：ok=False 或 remaining>0 都视为失败
        """
        if isinstance(result, tuple) and len(result) == 2:
            ok, remaining = result
            return bool(ok), int(remaining)
        return True, 0

    async def process_item(self, item: dict[str, Any], spider):
        if not self._enabled or self._sink is None:
            return item

        self._sequence += 1
        payload_dict = dict(item)
        url = str(payload_dict.pop("_url", "") or "")
        # R1-P1-10: DedupPipeline 挂的延迟提交信息，不进 data 字段
        dedup_commit = payload_dict.pop("_antcode_dedup", None)

        data_json = json.dumps(payload_dict, ensure_ascii=False)
        item_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        raw = await self._sink.write_item(
            item_id=item_id,
            item_type="default",
            data_json=data_json,
            url=url,
            timestamp=timestamp,
            sequence=self._sequence,
        )
        ok, n_written = self._normalize_write_result(raw)
        if ok:
            # P1-27 修复: 只有 sink 真正 ack 了 flush 才算 written。
            # gateway 模式 buffer 命中时 n_written=0（还没送出去，不能算成功）,
            # 触发 flush 时 n_written = 本批次条数一次性累加。
            # direct 模式每条 xadd 都是原子的，成功即 n_written=1。
            if n_written > 0:
                try:
                    spider.crawler.stats.inc_value(
                        "antcode/redis_items_written", count=n_written
                    )
                except Exception:
                    pass
            # R5-P2-14: 心跳
            if (
                self._heartbeat_interval > 0
                and self._sequence % self._heartbeat_interval == 0
            ):
                try:
                    await self._sink.write_meta(
                        {
                            "items_count": str(self._sequence),
                            "last_item_at": timestamp,
                        }
                    )
                except Exception as hexc:
                    logger.debug(f"heartbeat 写 meta 失败 (可忽略): {hexc}")
            # R1-P1-10: 成功后再提交 dedup SADD —— 只有 direct 模式的 Redis
            # sink 才有 SADD 语义；gateway 模式的去重集应该由 gateway 侧或
            # dedup pipeline 自己完成（DedupPipeline 早于此 pipeline 运行，
            # 前置 SISMEMBER 已经命中过；写回集合只需要保证 xadd 成功后再做）。
            if dedup_commit and isinstance(dedup_commit, dict):
                try:
                    inner_redis = getattr(self._sink, "_redis", None)
                    if inner_redis is not None:
                        dedup_key = str(dedup_commit.get("key", ""))
                        digest = str(dedup_commit.get("digest", ""))
                        ttl = int(dedup_commit.get("ttl_seconds", 0) or 0)
                        if dedup_key and digest:
                            added = await inner_redis.sadd(dedup_key, digest)
                            if ttl and added:
                                await inner_redis.expire(dedup_key, ttl)
                except Exception as dexc:
                    logger.warning(f"dedup 延迟 SADD 失败 (幂等，不影响本次): {dexc}")
        else:
            self._xadd_fail_count += 1
            try:
                spider.crawler.stats.inc_value("antcode/redis_xadd_failed")
            except Exception:
                pass
            if self._xadd_fail_count >= self._xadd_fail_threshold:
                from scrapy.exceptions import CloseSpider

                raise CloseSpider(
                    f"AntCodeRedisPipeline: sink 连续失败 "
                    f"{self._xadd_fail_count} 次，中止爬取"
                )

        return item

    async def close_spider(self, spider) -> None:
        if not self._enabled or self._sink is None:
            return
        stats = spider.crawler.stats
        items = int(stats.get_value("item_scraped_count", 0))
        errors = int(stats.get_value("log_count/ERROR", 0))
        xadd_failed_pre = int(stats.get_value("antcode/redis_xadd_failed", 0))
        elapsed_ms = 0.0
        if self._started_at:
            elapsed_ms = (datetime.now() - self._started_at).total_seconds() * 1000

        # P1-27: 决定 final meta 里发的 status——因为 close() 里会把 status
        # 一并发出去，我们先按当前已知信息给出乐观判定；close 完再核对，
        # 若 close 报告失败/有 remaining 就补记 xadd_failed，CLI 靠它判 exit code。
        pre_written = int(stats.get_value("antcode/redis_items_written", 0))
        if xadd_failed_pre > 0 or (items > 0 and pre_written == 0 and items > 0):
            optimistic_status = "failed"
        else:
            optimistic_status = "completed"

        close_ok = True
        remaining = 0
        try:
            raw_close = await self._sink.close(
                {
                    "status": optimistic_status,
                    "finished_at": datetime.now().isoformat(),
                    # items_count 送 scraped 总数（gateway 里最终的 items_count
                    # 反映的是抓到的总量，实际落地量看 CLI 侧 written 校核）
                    "items_count": str(items),
                    "errors_count": str(errors + xadd_failed_pre),
                    "duration_ms": str(elapsed_ms),
                }
            )
            close_ok, remaining = self._normalize_close_result(raw_close)
        except Exception as exc:
            close_ok = False
            logger.exception(f"sink.close 抛异常: {exc}")

        if not close_ok or remaining > 0:
            # P1-27: 最终 flush 失败——补一次 xadd_failed，让 crawl.py 里
            # ``if xadd_failed > 0: return 1`` 生效。
            try:
                stats.inc_value("antcode/redis_xadd_failed")
            except Exception:
                pass
            # 新增标记供 CLI 直接感知 close 阶段失败
            try:
                stats.set_value("antcode/final_flush_failed", 1)
                stats.set_value("antcode/final_flush_remaining", int(remaining))
            except Exception:
                pass

        final_xadd_failed = int(stats.get_value("antcode/redis_xadd_failed", 0))
        final_written = int(stats.get_value("antcode/redis_items_written", 0))
        final_status = (
            "failed"
            if (
                final_xadd_failed > 0
                or not close_ok
                or remaining > 0
                or (items > 0 and final_written < items)
            )
            else "completed"
        )
        logger.info(
            f"AntCodeRedisPipeline 完成: run_id={self._run_id} status={final_status} "
            f"scraped={items} written={final_written} xadd_failed={final_xadd_failed} "
            f"errors={errors} duration_ms={elapsed_ms:.0f} "
            f"close_ok={close_ok} remaining={remaining}"
        )
