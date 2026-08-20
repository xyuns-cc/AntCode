"""Engine-specific dependency wiring."""

from __future__ import annotations

from functools import partial
from typing import Any


def create_engine(
    config: Any,
    *,
    transport: Any,
    runtime_manager: Any,
    executor: Any,
    plugin_registry: Any,
    log_manager: Any,
    project_fetcher: Any,
    artifact_manager: Any,
    metrics_collector: Any,
    heartbeat_reporter: Any,
    tombstone_redis: Any = None,
) -> Any:
    """Build the Engine and connect metrics observers."""
    from antcode_worker.adaptive_limits import calculate_adaptive_limits
    from antcode_worker.engine.engine import Engine
    from antcode_worker.engine.policies import default_policies

    max_concurrent = getattr(config, "max_concurrent_tasks", 5)
    # 策略对象由 wiring 持有：引擎在 __init__ 与 apply_config_update 里**就地**改写
    # 它，所以这一份引用就是生效限额的实时视图。心跳读它而不是读 config——
    # config 在运行时 config_update 之后不会被回写，读它就会报出过期的限额。
    policies = default_policies()
    engine = Engine(
        transport=transport,
        executor=executor,
        flow_controller=_create_flow_controller(config),
        runtime_manager=runtime_manager,
        plugin_registry=plugin_registry,
        log_manager_factory=log_manager,
        project_fetcher=project_fetcher,
        artifact_manager=artifact_manager,
        policies=policies,
        max_concurrent=max_concurrent,
        memory_limit_mb=getattr(config, "task_memory_limit_mb", 0),
        cpu_limit_seconds=getattr(config, "task_cpu_time_limit_sec", 0),
        cancel_tombstones=_create_cancel_tombstones(config, transport, tombstone_redis),
        auto_resource_limit=bool(getattr(config, "auto_resource_limit", False)),
        adaptive_limits_provider=calculate_adaptive_limits,
        capacity_observer=partial(
            _set_worker_capacity,
            metrics_collector,
            heartbeat_reporter,
        ),
    )
    _connect_metrics(engine, metrics_collector, policies)
    return engine


def _create_cancel_tombstones(config: Any, transport: Any, tombstone_redis: Any) -> Any:
    """Bind cancel tombstones to the Worker identity that owns the ACL-scoped keys.

    tombstone 键收敛到 ``{ns}`` + ``{wid}``，wid 必须与 transport 用于
    ``task:ready``/``control`` 的同一个 worker_id，否则 Redis ACL 拒写。
    """
    from antcode_worker.engine.cancel_tombstones import CancelTombstones

    return CancelTombstones(
        redis_client=tombstone_redis,
        namespace=getattr(config, "redis_namespace", None),
        worker_id=str(getattr(transport, "_worker_id", "") or ""),
    )


def _connect_metrics(engine: Any, metrics_collector: Any, policies: Any) -> None:
    metrics_collector.set_state_manager(engine.state_manager)
    metrics_collector.set_scheduler(engine.scheduler)
    from antcode_worker.heartbeat.metric_models import EffectiveTaskLimits
    from antcode_worker.runtime.uv_manager import uv_manager

    metrics_collector.set_env_count_provider(uv_manager.get_env_count)
    metrics_collector.set_task_limits_provider(
        lambda: EffectiveTaskLimits(
            memory_limit_mb=policies.resource.memory_limit_mb,
            cpu_time_limit_sec=policies.resource.cpu_limit_seconds,
        )
    )
    engine.set_spider_stats_recorder(metrics_collector.record_spider_stats_file)
    engine.set_task_completed_recorder(metrics_collector.record_task_executed)


def _set_worker_capacity(
    metrics_collector: Any,
    heartbeat_reporter: Any,
    max_slots: int,
) -> None:
    metrics_collector.set_max_slots(max_slots)
    heartbeat_reporter.set_max_concurrent_tasks(max_slots)


def _create_flow_controller(config: Any) -> Any:
    from antcode_worker.transport.flow_control import (
        FlowControlConfig,
        FlowControlStrategy,
        create_flow_controller,
    )

    if not getattr(config, "flow_control_enabled", False):
        return None
    strategy = FlowControlStrategy(getattr(config, "flow_control_strategy", "token_bucket"))
    flow_config = FlowControlConfig(
        strategy=strategy,
        bucket_capacity=getattr(config, "flow_control_capacity", 200),
        refill_rate=getattr(config, "flow_control_rate", 100.0),
    )
    return create_flow_controller(strategy, flow_config)


__all__ = ["create_engine"]
