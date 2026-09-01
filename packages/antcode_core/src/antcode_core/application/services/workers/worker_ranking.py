"""把一轮负载探测装配成只读的 Worker 排名行（``GET /workers/load/ranking``）。

与派发共用同一份探测读数、同一条硬门禁——口径不一致的话，页面上标着"可用"的机器会派不
进去，而那种矛盾没人查得动。但两者要的东西不同，所以分开放：

- **派发**只需挑出最优的那一台，判据全落在 ``normalize_worker_metrics`` 的六个键上；
- **排名**要把每一台都摆给运维看，包括只存在于落库 ``worker.metrics`` 列、判据从不读的
  展示字段（``taskCount`` / ``uptime`` / ``projectCount`` ……）。

这是全仓唯一真需要把落库列合并进来的地方，也因此是唯一必须先过
``persisted_worker_metrics`` 契约检查的地方：那一列是 jsonb，存进数组或被二次编码成字符串
都发生过，直接喂给 ``dict.update`` 时，一台的坏列就是整页 500。
"""

from __future__ import annotations

from typing import Any

from antcode_core.application.services.workers.worker_liveness import heartbeat_age_ms
from antcode_core.application.services.workers.worker_load_score import PERCENT_FULL, calculate_load_score
from antcode_core.application.services.workers.worker_resource_probe import (
    merge_worker_metrics,
    persisted_worker_metrics,
)


def _verdict(balancer: Any, worker: Any, probed: Any) -> tuple[dict[str, Any], float, bool]:
    """一台机器的展示指标、评分、可用性。

    ``BaseException`` 而不是 ``Exception``，口径与 ``collect_dispatch_candidates`` 对齐：
    ``gather(return_exceptions=True)`` 会把 ``CancelledError`` 这类非 ``Exception`` 的
    ``BaseException`` 原样放进结果列表（探测在 ``_refresh_resources`` 里是共享的 inflight
    task，一个调用方断开就会把它取消，殃及同时在等的其他调用方）。只判 ``Exception`` 时
    它会漏进下面的合并，再被 ``dict.update`` 打成 500。
    """
    if isinstance(probed, BaseException) or not probed:
        return {}, PERCENT_FULL, False
    metrics = merge_worker_metrics(persisted_worker_metrics(worker), probed)
    return metrics, calculate_load_score(metrics), balancer.is_worker_available(worker, metrics)


def _ranking_row(balancer: Any, worker: Any, probed: Any) -> dict[str, Any]:
    metrics, score, available = _verdict(balancer, worker, probed)
    return {
        "worker_id": worker.public_id,
        "name": worker.name,
        "host": worker.host,
        "port": worker.port,
        "region": worker.region,
        "load_score": score,
        "available": available,
        "metrics": metrics,
        # 曾经叫 latency_ms，但它从来就是 now - last_heartbeat，不是网络往返；这个仓库没有
        # 任何 Master→Worker 的探活通道能测出往返（派发单向走 Redis Stream，
        # worker_connection_service.test_connection 也只标 heartbeat）。判据删掉之后更不能
        # 留一个名字说谎的展示字段。
        "heartbeat_age_ms": heartbeat_age_ms(worker.last_heartbeat),
    }


def build_worker_rankings(balancer: Any, workers: Any, resource_results: Any) -> list[dict[str, Any]]:
    """按评分升序装配排名行；``resource_results`` 与 ``workers`` 一一对应。"""
    rows = [_ranking_row(balancer, worker, probed) for worker, probed in zip(workers, resource_results, strict=False)]
    rows.sort(key=lambda row: row["load_score"])
    return rows


__all__ = ["build_worker_rankings"]
