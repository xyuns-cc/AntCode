"""
心跳上报器

定期发送心跳维护与 Gateway/Redis 的连接状态。
集成Worker能力检测。

Requirements: 10.1, 10.3
"""

import asyncio
import contextlib
import platform
import time
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any

from loguru import logger

# CapabilityDetector 现居 capability_detector.py；此处显式再导出，
# 因为 app/capabilities.py 与既有测试仍按 heartbeat.reporter 路径导入。
from antcode_worker.heartbeat.capability_detector import (
    CapabilityDetector as CapabilityDetector,
)
from antcode_worker.heartbeat.capability_detector import get_capability_detector
from antcode_worker.heartbeat.lease_timing import (
    DEFAULT_RENEW_AFTER_MS,
    MILLISECONDS_PER_SECOND,
    HeartbeatClock,
    LeaseRenewalWindow,
    LeaseRenewalWindowError,
    adopt_server_window,
    reconnect_delay_seconds,
)
from antcode_worker.heartbeat.renewal_supervision import classify_loop_termination, report_fatal_termination
from antcode_worker.heartbeat.reporter_models import Heartbeat, Metrics, OSInfo, SpiderStats
from antcode_worker.heartbeat.reporter_protocols import MetricsCollectorProtocol as MetricsCollectorProtocol
from antcode_worker.heartbeat.reporter_protocols import TransportProtocol as TransportProtocol
from antcode_worker.transport.generation import raise_if_generation_lost

MIB_IN_BYTES = 1024 * 1024
GIB_IN_BYTES = 1024 * MIB_IN_BYTES


class HeartbeatState(StrEnum):
    """心跳状态"""

    IDLE = "idle"  # 空闲
    RUNNING = "running"  # 运行中
    DEGRADED = "degraded"  # 降级模式（连续失败）
    RECONNECTING = "reconnecting"  # 重连中
    STOPPED = "stopped"  # 已停止


class HeartbeatReporter:
    """
    心跳上报器

    心跳同时是 lease 续期信号：节拍由服务端 ``renew_after_ms`` 驱动，
    并强制维持 ``interval < TTL/2``。连续失败只影响"下次重连尝试"的时间点，
    不允许拖慢续期节拍（B6）。

    Requirements: 10.1, 10.3
    """

    DEFAULT_INTERVAL = DEFAULT_RENEW_AFTER_MS // MILLISECONDS_PER_SECOND
    MAX_CONSECUTIVE_FAILURES = 5
    # 续期失败后收紧节拍，在 TTL 内多抢几次续期机会。
    FAILURE_RETRY_INTERVAL_SECONDS = 1.0
    # 循环自身异常（非续期失败）后的重试间隔，同样必须远小于 TTL。
    LOOP_ERROR_RETRY_SECONDS = 1.0
    RECONNECT_BACKOFF_BASE = 2.0  # 重连退避基数
    RECONNECT_BACKOFF_MAX = 300.0  # 最大重连退避时间（秒），只推迟重连

    def __init__(
        self,
        transport: TransportProtocol,
        worker_id: str,
        metrics_collector: MetricsCollectorProtocol | None = None,
        version: str = "",
        max_concurrent_tasks: int = 5,
        name: str = "",
        host: str = "",
        port: int = 0,
        region: str = "",
        *,
        renewal_window: LeaseRenewalWindow | None = None,
        clock: HeartbeatClock | None = None,
    ):
        self._transport = transport
        self._worker_id = worker_id
        self._renewal_window = renewal_window or LeaseRenewalWindow()
        self._clock = clock or HeartbeatClock()
        self._next_reconnect_at = 0.0
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_heartbeat_time: float | None = None
        self._consecutive_failures = 0
        self._state = HeartbeatState.IDLE

        self._metrics_collector = metrics_collector
        self._version = version
        self._max_concurrent_tasks = max_concurrent_tasks
        self._name = name
        self._host = host
        self._port = port
        self._region = region
        self._on_disconnect: Callable[[], Any] | None = None
        self._on_reconnect: Callable[[], Any] | None = None
        self._fatal_error_handler: Callable[[BaseException], Any] | None = None

        # 重连相关
        self._reconnect_attempts = 0
        self._last_reconnect_time: float | None = None
        self._reconnect_task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    @property
    def last_heartbeat_time(self) -> float | None:
        """上次心跳时间"""
        return self._last_heartbeat_time

    @property
    def interval(self) -> float:
        """当前续期节拍（秒），由服务端 renew_after_ms 驱动"""
        return self._renewal_window.interval_seconds

    @property
    def renewal_window(self) -> LeaseRenewalWindow:
        """当前租约续期时序快照"""
        return self._renewal_window

    @property
    def consecutive_failures(self) -> int:
        """连续失败次数"""
        return self._consecutive_failures

    @property
    def state(self) -> HeartbeatState:
        """当前状态"""
        return self._state

    @property
    def reconnect_attempts(self) -> int:
        """重连尝试次数"""
        return self._reconnect_attempts

    def set_disconnect_callback(self, callback: Callable[[], Any]) -> None:
        """设置断开连接回调"""
        self._on_disconnect = callback

    def set_reconnect_callback(self, callback: Callable[[], Any]) -> None:
        """设置重连成功回调"""
        self._on_reconnect = callback

    def set_metrics_collector(self, collector: MetricsCollectorProtocol) -> None:
        """设置指标收集器"""
        self._metrics_collector = collector

    def set_max_concurrent_tasks(self, max_concurrent_tasks: int) -> None:
        """Keep fallback heartbeat capacity aligned with live Engine config."""
        if isinstance(max_concurrent_tasks, bool) or max_concurrent_tasks < 1:
            raise ValueError("max_concurrent_tasks 必须是正整数")
        self._max_concurrent_tasks = max_concurrent_tasks

    def update_worker_id(self, worker_id: str) -> None:
        """更新 Worker ID"""
        old_id = self._worker_id
        self._worker_id = worker_id
        logger.info(f"心跳上报器 worker_id 已更新: {old_id} -> {worker_id}")

    async def start(self, interval: int = DEFAULT_INTERVAL) -> None:
        """启动心跳（lease 续期）循环。

        ``interval`` 必须来自服务端协商的 ``renew_after_ms``；违反
        ``interval < TTL/2`` 时直接抛错，不做静默 clamp（clamp 会让 Worker
        以为租约还在，而 master 已经把 run 补派出去）。
        """
        if self._running:
            return

        self.apply_lease_renew_after_ms(interval * MILLISECONDS_PER_SECOND)
        # 启动前 lifecycle 已跑过一次 lease_renew，传输层已握有服务端权威时序；
        # 不立刻采纳的话，第一拍的续期超时（TTL/2）仍按本地引导 TTL 计算。
        self._adopt_server_lease_cadence()
        self._running = True
        self._state = HeartbeatState.RUNNING
        self._task = asyncio.create_task(self._loop())
        self._task.add_done_callback(self._on_loop_finished)
        logger.info(f"心跳上报已启动: interval={self._renewal_window.interval_seconds}s")

    def set_fatal_error_handler(self, handler: Callable[[BaseException], Any]) -> None:
        """注册续期循环终止时的进程级停机通道，理由见 ``renewal_supervision``。"""
        self._fatal_error_handler = handler

    def _on_loop_finished(self, task: asyncio.Task) -> None:
        """续期循环终止即视为致命：先让状态说真话，再上报。"""
        if self._task is not task:
            return  # stop() 已接管并置空，属正常停止路径
        self._running = False
        self._state = HeartbeatState.STOPPED
        error = classify_loop_termination(task)
        if error is not None:
            report_fatal_termination(error, self._fatal_error_handler)

    def apply_lease_renew_after_ms(self, renew_after_ms: int) -> None:
        """只调节拍、沿用当前 TTL；仅用于启动引导（租约尚未签发）。

        租约签发后由 ``adopt_server_window`` 用服务端权威 TTL 重建窗口。
        """
        self._install_window(self._renewal_window.with_renew_after_ms(renew_after_ms))

    def _install_window(self, window: LeaseRenewalWindow) -> None:
        if window == self._renewal_window:
            return
        self._renewal_window = window
        logger.info(f"lease 续期时序已更新: ttl_ms={window.ttl_ms} renew_after_ms={window.renew_after_ms}")

    async def stop(self) -> None:
        """停止心跳上报"""
        self._running = False
        self._state = HeartbeatState.STOPPED
        tasks = (("心跳", self._task), ("重连", self._reconnect_task))
        self._task = None
        self._reconnect_task = None
        failures = await _stop_reporter_tasks(tasks)
        if failures:
            raise ExceptionGroup("心跳上报停止失败", failures)
        logger.info("心跳上报已停止")

    async def send_heartbeat(self) -> bool:
        """发送心跳"""
        if not self._transport.is_connected:
            self._record_heartbeat_failure("传输层未连接")
            return False

        try:
            heartbeat = await self._build_heartbeat()
            start = time.time()

            success = await self._transport.send_heartbeat(heartbeat)

            latency = (time.time() - start) * 1000

            if success:
                self._last_heartbeat_time = time.time()
                self._consecutive_failures = 0
                self._reconnect_attempts = 0
                self._next_reconnect_at = 0.0

                # 更新指标收集器
                if self._metrics_collector:
                    try:
                        self._metrics_collector.update_heartbeat_ts(self._last_heartbeat_time)
                        self._metrics_collector.reset_reconnect_count()
                    except Exception:
                        pass

                # 从降级模式恢复
                if self._state == HeartbeatState.DEGRADED:
                    self._state = HeartbeatState.RUNNING
                    logger.info("心跳恢复正常，退出降级模式")

                logger.debug(f"心跳发送成功: latency={latency:.1f}ms")
                return True
            else:
                self._record_heartbeat_failure("心跳发送失败")
                return False

        except Exception as exc:
            raise_if_generation_lost(exc)
            self._record_heartbeat_failure(f"心跳发送异常: {exc}")
            return False

    def _record_heartbeat_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        logger.warning(f"{reason}: consecutive={self._consecutive_failures}")

    async def _loop(self) -> None:
        """lease 续期主循环：任何失败路径都不得让节拍慢于 TTL。"""
        while self._running:
            try:
                await self._run_renewal_cycle()
            except asyncio.CancelledError:
                break
            except LeaseRenewalWindowError:
                # 服务端下发了会掉租约的节拍：炸穿主循环让故障可见。
                # 吞掉它等于 Worker 以为自己还持有租约，而 master 已把 run 补派出去。
                raise
            except Exception as exc:
                raise_if_generation_lost(exc)
                logger.error(f"心跳循环异常: {exc}")
                await self._clock.sleep(self.LOOP_ERROR_RETRY_SECONDS)

    async def _run_renewal_cycle(self) -> None:
        """一个续期周期：尝试续期 → 采纳服务端节拍 → 按需后台重连 → 等待下一拍。"""
        renewed = await self._attempt_renewal()
        self._adopt_server_lease_cadence()
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            self._schedule_reconnect()
        await self._clock.sleep(self._next_delay_seconds(renewed))

    def _adopt_server_lease_cadence(self) -> None:
        """采纳服务端权威时序；越界或缺 TTL 由 ``adopt_server_window`` 显式抛错。"""
        window = adopt_server_window(self._transport)
        if window is not None:
            self._install_window(window)

    async def _attempt_renewal(self) -> bool:
        """单次续期尝试，超过 TTL/2 视为失败。

        有界的单次尝试 + 严格小于 TTL/2 的等待 ⇒ 相邻两次尝试间隔 < TTL。
        """
        try:
            return await asyncio.wait_for(
                self.send_heartbeat(),
                timeout=self._renewal_window.max_attempt_gap_seconds,
            )
        except TimeoutError:
            self._record_heartbeat_failure("心跳续期超时")
            return False

    def _next_delay_seconds(self, renewed: bool) -> float:
        """下一次续期尝试前的等待时长；降级/重连状态一律不放慢节拍。"""
        interval = self._renewal_window.interval_seconds
        if renewed:
            return interval
        return min(self.FAILURE_RETRY_INTERVAL_SECONDS, interval)

    def _schedule_reconnect(self) -> None:
        """把重连（含指数退避）放到后台任务，主循环只负责按拍续期。"""
        task = self._reconnect_task
        if task is not None and not task.done():
            return
        if task is not None:
            self._reconnect_task = None
            failure = task.exception()
            if failure is not None:
                raise failure
        if self._clock.monotonic() < self._next_reconnect_at:
            return
        self._reconnect_task = asyncio.create_task(self._handle_consecutive_failures())

    async def _handle_consecutive_failures(self) -> None:
        """处理连续失败"""
        logger.warning(f"心跳连续失败 {self._consecutive_failures} 次，触发重连")

        # 进入降级模式
        self._state = HeartbeatState.DEGRADED

        # 更新指标
        if self._metrics_collector:
            with contextlib.suppress(Exception):
                self._metrics_collector.increment_reconnect_count()

        # 触发断开连接回调
        if self._on_disconnect:
            try:
                result = self._on_disconnect()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"断开连接回调异常: {e}")

        # 尝试重连
        await self._attempt_reconnect()

        # 重置失败计数（避免重复触发）
        self._consecutive_failures = 0

    async def _attempt_reconnect(self) -> bool:
        """尝试重连。

        指数退避只推迟*下一次*重连尝试（``_next_reconnect_at``），
        不再 ``sleep`` 在调用栈里——那会把 lease 续期一起堵死。

        Returns:
            是否重连成功
        """
        self._state = HeartbeatState.RECONNECTING
        self._reconnect_attempts += 1
        backoff = reconnect_delay_seconds(
            self._reconnect_attempts,
            self.RECONNECT_BACKOFF_BASE,
            self.RECONNECT_BACKOFF_MAX,
        )
        self._next_reconnect_at = self._clock.monotonic() + backoff
        logger.info(f"尝试重连 (attempt={self._reconnect_attempts}, 下次退避={backoff:.1f}s)")

        try:
            success = await self._transport.reconnect()
        except Exception as e:
            logger.error(f"重连异常: {e}")
            self._state = HeartbeatState.DEGRADED
            return False

        if not success:
            logger.warning(f"重连失败 (attempts={self._reconnect_attempts})")
            self._state = HeartbeatState.DEGRADED
            return False
        return await self._finish_reconnect()

    async def _finish_reconnect(self) -> bool:
        """重连成功后的状态复位与回调。"""
        logger.info(f"重连成功 (attempts={self._reconnect_attempts})")
        self._state = HeartbeatState.RUNNING
        self._reconnect_attempts = 0
        self._next_reconnect_at = 0.0
        self._last_reconnect_time = time.time()

        if self._on_reconnect:
            try:
                result = self._on_reconnect()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"重连成功回调异常: {e}")
        return True

    async def _build_heartbeat(self) -> Heartbeat:
        """构建心跳数据"""
        return Heartbeat(
            worker_id=self._worker_id,
            name=self._name,
            host=self._host,
            port=self._port,
            region=self._region,
            status="online",
            metrics=await self._get_metrics(),
            os_info=self._get_os_info(),
            timestamp=datetime.now(),
            capabilities=self._get_capabilities(),
            version=self._version,
        )

    async def _get_metrics(self) -> Metrics:
        """获取系统指标"""
        if not self._metrics_collector:
            return Metrics(max_concurrent_tasks=self._max_concurrent_tasks)
        collected = await self._metrics_collector.collect(use_cache=False)
        worker = collected.worker
        return Metrics(
            cpu=self._bounded_percent(collected.cpu.percent),
            memory=self._bounded_percent(collected.memory.percent),
            disk=self._bounded_percent(collected.disk.percent),
            running_tasks=worker.running_slots,
            max_concurrent_tasks=worker.max_slots or self._max_concurrent_tasks,
            task_count=worker.total_tasks_executed,
            project_count=worker.project_count,
            env_count=worker.env_count,
            queued_tasks=worker.queue_depth,
            cpu_cores=collected.cpu.count,
            memory_total_bytes=int(collected.memory.total_mb * MIB_IN_BYTES),
            memory_used_bytes=int(collected.memory.used_mb * MIB_IN_BYTES),
            memory_available_bytes=int(collected.memory.available_mb * MIB_IN_BYTES),
            disk_total_bytes=int(collected.disk.total_gb * GIB_IN_BYTES),
            disk_used_bytes=int(collected.disk.used_gb * GIB_IN_BYTES),
            disk_free_bytes=int(collected.disk.free_gb * GIB_IN_BYTES),
            uptime_seconds=worker.uptime_seconds,
            spider_stats=self._get_spider_stats(),
        )

    def _get_spider_stats(self) -> SpiderStats | None:
        if not self._metrics_collector:
            return None
        stats = self._metrics_collector.get_spider_stats()
        if not stats:
            return None
        return SpiderStats(
            request_count=stats.get("request_count", 0),
            response_count=stats.get("response_count", 0),
            item_scraped_count=stats.get("item_scraped_count", 0),
            error_count=stats.get("error_count", 0),
            avg_latency_ms=stats.get("avg_latency_ms", 0.0),
            requests_per_minute=stats.get("requests_per_minute", 0.0),
            status_codes=stats.get("status_codes", {}),
            domain_stats=stats.get("domain_stats", []),
        )

    @staticmethod
    def _bounded_percent(value: float) -> float:
        return round(max(0.0, min(100.0, value)), 1)

    def _get_os_info(self) -> OSInfo:
        """获取操作系统信息"""
        if self._metrics_collector:
            info = self._metrics_collector.get_os_info()
            return OSInfo(
                os_type=info.get("os_type", ""),
                os_version=info.get("os_version", ""),
                python_version=info.get("python_version", ""),
                machine_arch=info.get("machine_arch", ""),
            )

        return OSInfo(
            os_type=platform.system(),
            os_version=platform.release(),
            python_version=platform.python_version(),
            machine_arch=platform.machine(),
        )

    def _get_capabilities(self) -> dict:
        """获取Worker能力"""
        detector = get_capability_detector()
        return detector.detect_all()


# 全局实例
_heartbeat_reporter: HeartbeatReporter | None = None


async def _stop_reporter_tasks(
    tasks: tuple[tuple[str, asyncio.Task | None], ...],
) -> list[Exception]:
    failures: list[Exception] = []
    for label, task in tasks:
        if task is None:
            continue
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            continue
        except Exception as exc:
            logger.opt(exception=exc).error("{} task 停止失败", label)
            failures.append(exc)
    return failures


def get_heartbeat_reporter() -> HeartbeatReporter | None:
    """获取全局心跳上报器"""
    return _heartbeat_reporter


def init_heartbeat_reporter(
    transport: TransportProtocol,
    worker_id: str,
    metrics_collector: MetricsCollectorProtocol | None = None,
    version: str = "",
    max_concurrent_tasks: int = 5,
    name: str = "",
    host: str = "",
    port: int = 0,
    region: str = "",
) -> HeartbeatReporter:
    """初始化全局心跳上报器"""
    global _heartbeat_reporter
    _heartbeat_reporter = HeartbeatReporter(
        transport=transport,
        worker_id=worker_id,
        metrics_collector=metrics_collector,
        version=version,
        max_concurrent_tasks=max_concurrent_tasks,
        name=name,
        host=host,
        port=port,
        region=region,
    )
    return _heartbeat_reporter
