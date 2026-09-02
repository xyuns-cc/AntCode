"""爬虫批次告警评估器。

上一版 crawl 告警是这样死的：``PUT /crawl/metrics/config`` 改的是 web_api
进程里的单例，而告警在 master 进程的另一个单例里求值——返回 200，什么都没变；
指标源又是一条没有写入者的 Redis Stream，阈值永远比不出结果。

据此立的约束：

- **没有配置面。** 停滞窗口由调用方把自己的既有常量传进来，级别与来源是本
  模块常量。不存在"第二个进程持有分叉状态"这个形状，也就不可能再犯那条
  跨进程单例的错。
- **只消费已经证明有写入者的信号**，而且就是调用方在同一次 tick 里已经拿在
  手上的那一份：``task_executions`` 的状态聚合（worker → result_loop →
  TaskResultCommitter 写入）与 ``crawl_batches.status``（本 loop 的 CAS 写入）。
  不另开数据源，也就没有"指标恒为 0"的余地。
- 求值发生在 ``crawl_batch_status_loop`` 的 tick 里：leader-gated、单进程、
  单实例，多副本 master 不会把同一条告警 fanout N 次。

投递复用通用 ``alert_service``（飞书/webhook 等），不建第二条告警管道。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from antcode_core.application.services.alert import alert_service
from antcode_core.domain.models import CrawlBatch
from antcode_core.domain.models.enums import BatchStatus
from loguru import logger

# alert_service 的 auto_alert_levels 默认是 ["ERROR", "CRITICAL"]，未被订阅的
# 级别会在渠道侧被 ERROR_CHANNEL_LEVEL_FILTERED 丢掉。WARNING 在默认配置下
# 就是一条永远发不出去的告警，所以两条规则都用 ERROR。
_ALERT_LEVEL = "ERROR"
_ALERT_SOURCE = "crawl"


@dataclass
class _ProgressMark:
    """某批次最近一次「结算数发生变化」的观测点。"""

    settled_runs: int
    observed_at: float
    alerted: bool


class CrawlBatchAlerts:
    def __init__(self) -> None:
        self._marks: dict[str, _ProgressMark] = {}

    def retain(self, running_batch_ids: set[str]) -> None:
        """丢掉已经离开 RUNNING 的批次观测点。

        master 是长驻进程，不回收的话每个见过的批次都会永久占一条记录。
        """
        self._marks = {key: mark for key, mark in self._marks.items() if key in running_batch_ids}

    async def notify_settled(self, batch: CrawlBatch, new_status: str, detail: str) -> None:
        """批次刚被推成终态时调用——即 ``_cas_terminate`` 返回 True 的那一次。

        只有 FAILED 值得叫醒人：COMPLETED 是正常收尾，CANCELLED 是用户自己点的。
        这里不需要任何去重状态：条件 UPDATE 保证一个批次离开 RUNNING 这件事
        全局只成功一次，告警天然与它一一对应。
        """
        if new_status != BatchStatus.FAILED.value:
            return
        await self._send(
            f"爬取批次失败: batch_id={batch.public_id} name={batch.name} {detail}",
            rate_key=f"crawl_batch_failed|{batch.public_id}",
        )

    async def observe_progress(self, batch: CrawlBatch, stat: dict[str, int], *, stall_after: float) -> None:
        """RUNNING 批次每 tick 调一次，检测「有在途 run 却迟迟不结算」。

        这是状态推导唯一看不见的故障形状：seed 全部派完之后，空转与未派完
        两条超时兜底都不再生效，批次会带着永不结算的 run 永久停在 RUNNING
        （例如 lease sweeper 的 eviction 回调抛错后 run 被搁浅，那条 lease
        已经删掉，下一轮 sweep 不会再看到它）。

        只在 ``active > 0`` 时判定：``active == 0`` 的不推进批次已经由那两条
        兜底收敛成 FAILED，会走 ``notify_settled``，不该再告警一次。
        """
        settled = stat["total"] - stat["active"]
        mark = self._marks.get(batch.public_id)
        now = time.monotonic()
        if mark is None or mark.settled_runs != settled:
            # 结算数一变就重新计时并解除已告警标记：停滞恢复后再次停滞要能再报。
            self._marks[batch.public_id] = _ProgressMark(settled, now, alerted=False)
            return
        if mark.alerted or stat["active"] <= 0 or now - mark.observed_at < stall_after:
            return
        mark.alerted = True
        await self._send(
            f"爬取批次停滞: batch_id={batch.public_id} name={batch.name} "
            f"在途 {stat['active']} 条 run 超过 {stall_after:.0f}s 没有任何结算 "
            f"(已结算 {settled}/{stat['total']})",
            rate_key=f"crawl_batch_stalled|{batch.public_id}",
        )

    @staticmethod
    async def _send(message: str, *, rate_key: str) -> None:
        """rate_key 必须稳定：消息里带着瞬时计数，用默认键的话每条哈希都不同，
        限流形同虚设。

        投递异常在这里收口而不是抛回 loop：批次终态在调用点之前已经落库，
        让一个不可达的 webhook 把异常带回去会连坐后面的推导步骤。
        """
        try:
            await alert_service.send_alert(
                message=message,
                level=_ALERT_LEVEL,
                source=_ALERT_SOURCE,
                rate_key=rate_key,
            )
        except Exception as exc:
            logger.warning(f"爬取批次告警投递失败: {rate_key} err={exc}")


crawl_batch_alerts = CrawlBatchAlerts()
