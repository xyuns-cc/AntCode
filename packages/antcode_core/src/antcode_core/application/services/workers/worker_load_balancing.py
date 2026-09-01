"""回答"现在哪台在线 Worker 最闲、够不够格接活"。

从 ``worker_dispatcher`` 拆出来的理由是两条失效线本来就不同：这里失效的形状永远是
**选不出节点**（指标读不回来、全部过了硬门禁、能力不匹配），它不会让一台已经选中的
Worker 收不到任务；反过来，Redis 写不进去也不该被说成"没有节点"。两者混在一个文件里，
"任务不动了"就只剩一句"无符合条件节点"可查。

而且它有两个根本不经过 dispatcher 的消费者：``scheduler/execution_resolver`` 的 AUTO
选节点、``routes/v1/workers_query`` 的 ``/workers/best`` 与负载排名。
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from antcode_core.application.services.workers.worker_capability_routing import (
    capability_requirement_label,
    has_render_capability,
    resolve_selection_capabilities,
    supports_task_types,
)
from antcode_core.application.services.workers.worker_load_score import calculate_load_score
from antcode_core.application.services.workers.worker_ranking import build_worker_rankings
from antcode_core.application.services.workers.worker_registration_gate import filter_registration_ready_workers
from antcode_core.application.services.workers.worker_resource_probe import (
    WorkerMetricsUnavailableError,
    collect_dispatch_candidates,
    probe_failure_suffix,
    probe_worker_resources,
    warn_unreadable_workers,
)
from antcode_core.domain.models import Worker, WorkerStatus

DEFAULT_RANKING_SIZE = 10


def _passes_placement_constraints(worker: Any, *, exclude_workers: Any, region: str | None, tags: Any) -> bool:
    """与能力无关的三条约束：显式排除、区域、标签。"""
    if exclude_workers and worker.id in exclude_workers:
        return False
    if region and worker.region != region:
        return False
    if not tags:
        return True
    return any(tag in (worker.tags or []) for tag in tags)


def _passes_capability_constraints(
    worker: Any,
    capabilities: Any,
    *,
    require_render: bool,
    require_task_type: str | frozenset[str] | None,
) -> bool:
    """T6-T4b: Worker 必须显式声明能力，缺失或格式错误时 fail-closed。"""
    if require_render and not has_render_capability(capabilities):
        logger.debug(f"节点 [{worker.name}] 无渲染能力，跳过")
        return False
    if not require_task_type:
        return True
    return supports_task_types(capabilities, require_task_type)


def _warn_no_eligible_worker(*, require_task_type: str | frozenset[str] | None, require_render: bool) -> None:
    """落选原因必须点名，否则运维分不清是能力没装还是压根没有节点。"""
    if require_task_type:
        logger.warning(
            f"无支持 task_type={capability_requirement_label(require_task_type)} 的可用节点 "
            "(检查 worker 侧 WORKER_ENABLE_RULE_PLUGIN 等 env)"
        )
        return
    logger.warning("无符合条件的渲染节点" if require_render else "无符合条件节点")


def _lowest_load_worker(scored_candidates: Any) -> Any:
    scored_workers = []
    for worker, metrics in scored_candidates:
        score = calculate_load_score(metrics)
        scored_workers.append((worker, score))
        logger.debug(f"负载评分 [{worker.name}] {score}")

    scored_workers.sort(key=lambda item: item[1])
    logger.info(f"选中节点 [{scored_workers[0][0].name}] 评分:{scored_workers[0][1]}")
    return scored_workers[0][0]


class WorkerLoadBalancer:
    """负载均衡器"""

    # 硬门禁：过了这条线就不再是"排在后面"，而是完全不派活。
    #
    # 这两个 90 与下面的 0.8 一样出自 70a26e9 的裸字面量，**没有任何依据留下**——同批
    # 引入的 WEIGHT_* 权重常量甚至从未被引用过。它们是按宿主口径的年代定的，而 CPU 与
    # 内存现已换成容器配额口径（f23f192 / 8074a8f），阈值的含义随之从"整机忙闲"变成
    # "本容器吃掉了自己配额的几成"。真机实测当前不误伤（三台各跑到自己配额约 50% 时全部
    # available、76.6% 仍不踢、只有真打满的那台被拦），但"实测没出事"不等于"90 是对的
    # 数"。上线首日应观察这条线附近的判定，有数据再定，不要在这里补一个编出来的理由。
    MAX_CPU_THRESHOLD = 90
    MAX_MEMORY_THRESHOLD = 90
    MAX_TASKS_RATIO = 0.8

    def __init__(self):
        self._resource_cache = {}
        self._resource_cache_time = {}
        self._resource_cache_ttl = 2.0
        self._resource_lock = asyncio.Lock()
        self._resource_inflight = {}

    def _get_cached_resources(self, worker):
        cached = self._resource_cache.get(worker.id)
        if not cached:
            return None
        cached_at = self._resource_cache_time.get(worker.id)
        if cached_at is None:
            return None
        if (asyncio.get_event_loop().time() - cached_at) > self._resource_cache_ttl:
            return None
        return cached

    async def _fetch_resources(self, worker):
        try:
            normalized = await probe_worker_resources(worker)
        # "读不到指标"必须原样冒出去。被下面这个 except 兜成 None，就又回到了
        # "Redis 抖一下 = 所有 Worker 一起消失，且没人知道为什么"。
        except WorkerMetricsUnavailableError:
            raise
        except Exception as e:
            logger.warning(f"资源指标无效: worker={worker.name}, error={e}")
            return None

        self._resource_cache[worker.id] = normalized
        self._resource_cache_time[worker.id] = asyncio.get_event_loop().time()
        return normalized

    async def _refresh_resources(self, worker):
        cached = self._get_cached_resources(worker)
        if cached is not None:
            return cached

        async with self._resource_lock:
            cached = self._get_cached_resources(worker)
            if cached is not None:
                return cached
            inflight = self._resource_inflight.get(worker.id)
            if not inflight:
                inflight = asyncio.create_task(self._fetch_resources(worker))
                self._resource_inflight[worker.id] = inflight

        try:
            return await inflight
        finally:
            async with self._resource_lock:
                if self._resource_inflight.get(worker.id) is inflight:
                    self._resource_inflight.pop(worker.id, None)

    async def score_worker(self, worker):
        """单台节点的负载评分，口径与 select_best_worker 排序时用的完全一致。"""
        return calculate_load_score(await self._refresh_resources(worker))

    def is_worker_available(self, worker, metrics=None):
        """检查节点可用性"""
        if worker.status != WorkerStatus.ONLINE:
            return False

        if metrics is None:
            metrics = self._get_cached_resources(worker)

        if not metrics:
            return False

        if metrics.get("cpu", 100) >= self.MAX_CPU_THRESHOLD:
            return False

        if metrics.get("memory", 100) >= self.MAX_MEMORY_THRESHOLD:
            return False

        running_tasks = metrics.get("runningTasks", 0)
        max_tasks = metrics.get("maxConcurrentTasks", 1)
        if max_tasks <= 0:
            return False
        return not running_tasks >= max_tasks * self.MAX_TASKS_RATIO

    async def _online_workers(self, region: str | None):
        query = Worker.filter(status=WorkerStatus.ONLINE.value)
        if region:
            query = query.filter(region=region)
        return await query.all()

    async def select_best_worker(
        self,
        *,
        workers=None,
        exclude_workers=None,
        region=None,
        tags=None,
        require_render=False,
        require_task_type: str | frozenset[str] | None = None,
    ):
        """选择最佳节点；任何一步筛空都返回 None，绝不退而求其次。"""
        if workers is None:
            workers = await self._online_workers(region)

        workers = await filter_registration_ready_workers(workers)
        if not workers:
            logger.warning("无可用节点")
            return None

        capabilities = await resolve_selection_capabilities(workers, require_render, require_task_type)
        filtered_workers = [
            worker
            for worker in workers
            if _passes_placement_constraints(worker, exclude_workers=exclude_workers, region=region, tags=tags)
            and _passes_capability_constraints(
                worker,
                capabilities[worker.id],
                require_render=require_render,
                require_task_type=require_task_type,
            )
        ]
        if not filtered_workers:
            _warn_no_eligible_worker(require_task_type=require_task_type, require_render=require_render)
            return None

        resource_results = await asyncio.gather(
            *[self._refresh_resources(worker) for worker in filtered_workers],
            return_exceptions=True,
        )
        candidates = collect_dispatch_candidates(self, filtered_workers, resource_results)
        warn_unreadable_workers(candidates)

        if not candidates.scored:
            shortage = "无符合条件的渲染节点" if require_render else "无符合条件节点"
            logger.warning("{}{}", shortage, probe_failure_suffix(candidates))
            return None
        return _lowest_load_worker(candidates.scored)

    async def get_workers_ranking(self, region=None, top_n=DEFAULT_RANKING_SIZE):
        """获取节点排名（只读展示，行的装配见 worker_ranking）。"""
        workers = await self._online_workers(region)
        resource_results = await asyncio.gather(
            *[self._refresh_resources(worker) for worker in workers],
            return_exceptions=True,
        )
        return build_worker_rankings(self, workers, resource_results)[:top_n]


worker_load_balancer = WorkerLoadBalancer()

__all__ = ["WorkerLoadBalancer", "worker_load_balancer"]
