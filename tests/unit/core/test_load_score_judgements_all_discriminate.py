"""打分里的每一项判据都必须真的参与区分，否则它是在悄悄改别人的权重。

原式有五项：CPU 0.30 / 内存 0.25 / 任务 0.20 / 延迟 0.15 / 成功率 0.10，加起来 1.0。
其中两项恒定：

- ``latency`` 取的是 ``now - worker.last_heartbeat`` 的毫秒数（心跳年龄），不是网络
  往返。心跳间隔默认 30s，采样到的年龄几乎总 ≥1000ms，而原式在 ``latency >= 1000``
  处直接取 100，于是恒定饱和；且该分段在 999→1000 处从 ~50 跳到 100，接不上对数段——
  偶尔"心跳刚落地"的那台会白拿最多 15 分，与它的忙闲无关；
- ``successRate`` 读顶层 ``metrics["successRate"]``，全仓无写入方，恒取默认 100，
  ``100 - 100 = 0``。

后果不是"这两项不起作用"，而是剩下三项在 0.75 上按比例放大：CPU 名义 0.30、实际
``0.30/0.75 = 0.40``。本轮刚把 CPU 与内存换成容器口径（f23f192 / 8074a8f），放大作用
在一个比设计时更敏感的量上。

**证伪方式**：把 ``calculate_load_score`` 换回五项加权式（含 latency 与 successRate），
除标注为非证伪项的两条以外全部变红。
"""

from __future__ import annotations

import math

from antcode_core.application.services.workers.worker_load_score import (
    LOAD_JUDGEMENTS,
    PERCENT_FULL,
    calculate_load_score,
)

# 原式的五个系数，逐字抄自 e11bf6a 的 worker_dispatcher.calculate_load_score。
_LEGACY_WEIGHT_CPU = 0.30
_LEGACY_WEIGHT_MEMORY = 0.25
_LEGACY_WEIGHT_TASKS = 0.20
_LEGACY_WEIGHT_LATENCY = 0.15
_LEGACY_WEIGHT_SUCCESS = 0.10

_LATENCY_FLOOR_MS = 10
_LATENCY_SATURATION_MS = 1000
_LATENCY_LOG_SCALE = 25

# 心跳间隔默认 30s（WORKER_HEARTBEAT_INTERVAL），所以采样到的心跳年龄在这个量级。
_TYPICAL_HEARTBEAT_AGE_MS = 30_000
_JUST_UNDER_SATURATION_MS = 999
# 心跳刚落地：对数段在这里只给 25 分，也就是延迟项几乎不收费。
_FRESH_HEARTBEAT_AGE_MS = 100

_EXPECTED_JUDGEMENT_COUNT = 3
_WEIGHT_SUM_TOLERANCE = 1e-9
_SCORE_DIGITS = 2


def _legacy_latency_score(latency_ms: float) -> float:
    """原式的延迟分段函数。"""
    if latency_ms <= _LATENCY_FLOOR_MS:
        return 0.0
    if latency_ms >= _LATENCY_SATURATION_MS:
        return PERCENT_FULL
    return min(PERCENT_FULL, max(0.0, _LATENCY_LOG_SCALE * math.log10(latency_ms / _LATENCY_FLOOR_MS)))


def _metrics(cpu: float, memory: float, *, running: int = 0, max_tasks: int = 10) -> dict[str, float | int]:
    return {
        "cpu": cpu,
        "memory": memory,
        "runningTasks": running,
        "queuedTasks": 0,
        "maxConcurrentTasks": max_tasks,
    }


def test_every_judgement_moves_the_score_on_its_own() -> None:
    """逐项单独变动，分数必须跟着动——这就是"参与区分"的定义。

    恒定项过不了这一条：把 latency 或 successRate 单独喂进去，分数纹丝不动。
    """
    baseline = _metrics(cpu=0, memory=0)
    baseline_score = calculate_load_score(baseline)

    moved = {
        "cpu": calculate_load_score(_metrics(cpu=PERCENT_FULL, memory=0)),
        "memory": calculate_load_score(_metrics(cpu=0, memory=PERCENT_FULL)),
        "tasks": calculate_load_score(_metrics(cpu=0, memory=0, running=10, max_tasks=10)),
    }

    assert len(moved) == len(LOAD_JUDGEMENTS), "有判据没被这条用例覆盖到"
    for name, score in moved.items():
        assert score > baseline_score, f"{name} 单独打满却没能把分数推高，它没在参与区分"


def test_unwritten_success_rate_cannot_change_the_score() -> None:
    """``successRate`` 全仓无写入方；就算有人塞进来也不该影响排序。

    原式会：塞 successRate=0 把分数拉高 10 分，塞 100 则不动——一个没有生产者的键
    却能改调度结果。
    """
    without = calculate_load_score(_metrics(cpu=20, memory=20))
    with_worst = calculate_load_score({**_metrics(cpu=20, memory=20), "successRate": 0})

    assert with_worst == without


def test_heartbeat_age_cannot_change_the_score() -> None:
    """心跳年龄是新鲜度，已由 ``worker_heartbeat_is_fresh`` 与 ONLINE/OFFLINE 判活把关。

    再作为软判据计一次是重复计分；而它恒饱和，等于只给所有人加同一个常数。
    """
    quiet = calculate_load_score({**_metrics(cpu=20, memory=20), "latency": _TYPICAL_HEARTBEAT_AGE_MS})
    fresh = calculate_load_score({**_metrics(cpu=20, memory=20), "latency": _JUST_UNDER_SATURATION_MS})

    assert quiet == fresh


def test_identically_loaded_workers_score_identically() -> None:
    """同样的负载必须得同样的分。这条钉死"延迟"项的本质：它量的是心跳相位。

    两台 Worker 各项负载完全相同，只是心跳落地的时刻不同——而心跳相位与"谁更闲"
    毫无关系。原式却给出不同的分：饱和的那台 26.0，心跳刚落地的那台 18.5，差 7.5 分；
    相位差拉满时差 15 分。这不是判据，是噪声。
    """
    load = _metrics(cpu=20, memory=20)

    legacy_stale = _legacy_score(load, latency_ms=_TYPICAL_HEARTBEAT_AGE_MS)
    legacy_fresh = _legacy_score(load, latency_ms=_JUST_UNDER_SATURATION_MS)
    assert legacy_stale != legacy_fresh, "前提失效：原式在这组数上并不受心跳相位影响"

    assert calculate_load_score(load) == calculate_load_score({**load, "latency": _JUST_UNDER_SATURATION_MS})


def test_a_fresher_heartbeat_no_longer_outranks_a_less_loaded_worker() -> None:
    """噪声足够大时会直接翻转排序：这条钉住它不再发生。

    延迟项占 0.15、跨度 0..100，也就是最多 15 分；除以 CPU 的 0.30 权重，等于它能盖过
    **50 个百分点**的 CPU 差距。这里取一组会翻的数：``busy`` 的 CPU 与内存都比 ``idle``
    高一倍，只因心跳恰好在 100ms 前落地就赢了 0.25 分。

    心跳间隔默认 30s，落在 100ms 窗口内的概率约 0.3%——不常见，但排序错了就是错了，
    而且 ``_latency_update_interval = 60`` 会把这个错误缓存住整整一分钟。
    """
    idle = _metrics(cpu=20, memory=20)
    busy = _metrics(cpu=40, memory=40)

    legacy_idle = _legacy_score(idle, latency_ms=_TYPICAL_HEARTBEAT_AGE_MS)
    legacy_busy = _legacy_score(busy, latency_ms=_FRESH_HEARTBEAT_AGE_MS)
    assert legacy_busy < legacy_idle, "前提失效：原式在这组数上并不会选错"

    assert calculate_load_score(busy) > calculate_load_score(idle), "更忙的那台仍然赢了"


def _legacy_score(metrics: dict[str, float | int], *, latency_ms: float) -> float:
    """复算 e11bf6a 的五项加权式，用来证明"原式确实会选错"。"""
    max_tasks = int(metrics["maxConcurrentTasks"])
    occupied = int(metrics["runningTasks"]) + int(metrics["queuedTasks"])
    task_score = min(PERCENT_FULL, occupied / max_tasks * PERCENT_FULL)
    success_score = PERCENT_FULL - PERCENT_FULL  # 无写入方，恒取默认 100
    return (
        float(metrics["cpu"]) * _LEGACY_WEIGHT_CPU
        + float(metrics["memory"]) * _LEGACY_WEIGHT_MEMORY
        + task_score * _LEGACY_WEIGHT_TASKS
        + _legacy_latency_score(latency_ms) * _LEGACY_WEIGHT_LATENCY
        + success_score * _LEGACY_WEIGHT_SUCCESS
    )


def test_weights_are_split_by_judgement_count_not_by_hardcoded_coefficients() -> None:
    """权重必须由项数推出来，这样增删判据不会静默改掉其余各项的实际权重。

    这正是被修的那个形状：五项里两项恒定，剩下三项的实际权重就从 0.30/0.25/0.20
    变成 0.40/0.33/0.27，而代码里一个字都没改。
    """
    saturated = calculate_load_score(_metrics(cpu=PERCENT_FULL, memory=PERCENT_FULL, running=10, max_tasks=10))
    assert saturated == PERCENT_FULL, "各项全满必须正好满分，否则权重之和不是 1"

    single = calculate_load_score(_metrics(cpu=PERCENT_FULL, memory=0))
    expected = round(PERCENT_FULL / len(LOAD_JUDGEMENTS), _SCORE_DIGITS)
    assert single == expected, "单项权重必须是 1/项数"

    # 三项两两同权：任何一项被单独打满，得分都必须一样。原式给出 30 / 25 / 20。
    assert calculate_load_score(_metrics(cpu=0, memory=PERCENT_FULL)) == expected
    assert calculate_load_score(_metrics(cpu=0, memory=0, running=10, max_tasks=10)) == expected


def test_judgement_count_matches_the_documented_three() -> None:
    """判据项数必须与文档一致；把 latency 或 successRate 任一项加回来它就红。

    它防的是"悄悄多一项"：多出来的那项即使恒定，也会把其余各项的实际权重按 3/4 缩小。
    """
    assert len(LOAD_JUDGEMENTS) == _EXPECTED_JUDGEMENT_COUNT
    assert abs(sum(1 / len(LOAD_JUDGEMENTS) for _ in LOAD_JUDGEMENTS) - 1.0) < _WEIGHT_SUM_TOLERANCE


def test_missing_metrics_score_worst() -> None:
    """**非证伪项**：修复前后都绿。

    它证明上面几条不是"永远返回同一个常数"——取不到指标时分数确实会变成满分，
    也就是"不可知按最坏算"这条语义没有在重构里丢掉。
    """
    assert calculate_load_score(None) == PERCENT_FULL
    assert calculate_load_score({}) == PERCENT_FULL
