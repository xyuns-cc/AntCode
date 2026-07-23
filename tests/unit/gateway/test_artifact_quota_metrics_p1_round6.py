"""P1-round6 5.3 回归:RunArtifactQuota.metrics() 暴露运维观察数据。

审查文档 round6 5.3:
`Artifact quota 是 4096-run 的进程内 LRU, 重启、多副本和驱逐均可重置;
无用户/项目/全局 quota`。

quota 语义边界(重启重置/多副本独立)已在 docstring 承认。本轮补齐
observability 面: metrics() 暴露 tracked_runs / evicted_runs /
exceeded_events, 让运维可观察 quota 的实际压力,识别:
- evicted_runs 高 → max_tracked_runs 需调大
- exceeded_events 高 → 合法请求接近上限或异常客户端

本测试锁死:
1. 初始快照全零
2. reserve 后 tracked_runs 增加
3. 超上限时 exceeded_events 递增(不重复计数 release 后再 reserve)
4. LRU 驱逐时 evicted_runs 递增
"""

from __future__ import annotations

import pytest
from antcode_gateway.services.artifact_quota import (
    RunArtifactQuota,
    RunArtifactQuotaExceeded,
)

_EXPECTED_TWO = 2
_EXPECTED_THREE = 3


def test_initial_metrics_all_zero():
    quota = RunArtifactQuota()
    m = quota.metrics()
    assert m == {"tracked_runs": 0, "evicted_runs": 0, "exceeded_events": 0}


def test_reserve_increases_tracked_runs():
    quota = RunArtifactQuota()
    quota.reserve("r-1", 100)
    quota.reserve("r-2", 100)
    assert quota.metrics()["tracked_runs"] == _EXPECTED_TWO


def test_exceeded_events_counted():
    quota = RunArtifactQuota(max_artifacts=1)
    quota.reserve("r-1", 10)
    with pytest.raises(RunArtifactQuotaExceeded):
        quota.reserve("r-1", 10)  # 第 2 件超 count 上限
    assert quota.metrics()["exceeded_events"] == 1
    with pytest.raises(RunArtifactQuotaExceeded):
        quota.reserve("r-1", 10)
    assert quota.metrics()["exceeded_events"] == _EXPECTED_TWO


def test_evicted_runs_counted():
    quota = RunArtifactQuota(max_tracked_runs=2)
    quota.reserve("r-1", 1)
    quota.reserve("r-2", 1)
    quota.reserve("r-3", 1)  # 应把 r-1 驱逐
    m = quota.metrics()
    assert m["tracked_runs"] == _EXPECTED_TWO
    assert m["evicted_runs"] == 1
    quota.reserve("r-4", 1)  # 再驱逐 r-2
    assert quota.metrics()["evicted_runs"] == _EXPECTED_TWO


def test_bytes_exceeded_also_counted():
    quota = RunArtifactQuota(max_total_bytes=100)
    quota.reserve("r-1", 60)
    with pytest.raises(RunArtifactQuotaExceeded):
        quota.reserve("r-1", 50)  # 超 total_bytes
    assert quota.metrics()["exceeded_events"] == 1
