"""
心跳上报器

定期发送心跳维护与 Gateway/Redis 的连接状态。
集成Worker能力检测。

Requirements: 10.1, 10.3
"""

import asyncio
import contextlib
import os
import platform
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from loguru import logger


class HeartbeatState(str, Enum):
    """心跳状态"""
    IDLE = "idle"                  # 空闲
    RUNNING = "running"            # 运行中
    DEGRADED = "degraded"          # 降级模式（连续失败）
    RECONNECTING = "reconnecting"  # 重连中
    STOPPED = "stopped"            # 已停止


class TransportProtocol(Protocol):
    """传输层协议"""

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        ...

    async def send_heartbeat(self, heartbeat: Any) -> bool:
        """发送心跳"""
        ...

    async def reconnect(self) -> bool:
        """重连"""
        ...


class MetricsCollectorProtocol(Protocol):
    """指标收集器协议"""

    def get_metrics(self) -> dict:
        """获取系统指标"""
        ...

    def get_os_info(self) -> dict:
        """获取操作系统信息"""
        ...

    def get_spider_stats(self) -> dict | None:
        """获取爬虫统计"""
        ...

    def update_heartbeat_ts(self, ts: float | None = None) -> None:
        """更新心跳时间戳"""
        ...

    def increment_reconnect_count(self) -> None:
        """增加重连计数"""
        ...

    def reset_reconnect_count(self) -> None:
        """重置重连计数"""
        ...


@dataclass
class OSInfo:
    """操作系统信息"""

    os_type: str = ""
    os_version: str = ""
    python_version: str = ""
    machine_arch: str = ""


@dataclass
class SpiderStats:
    """爬虫统计"""

    request_count: int = 0
    response_count: int = 0
    item_scraped_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    requests_per_minute: float = 0.0
    status_codes: dict = field(default_factory=dict)


@dataclass
class Metrics:
    """系统指标"""

    cpu: float = 0.0
    memory: float = 0.0
    disk: float = 0.0
    running_tasks: int = 0
    max_concurrent_tasks: int = 5
    task_count: int = 0
    project_count: int = 0
    env_count: int = 0
    spider_stats: SpiderStats | None = None


@dataclass
class Heartbeat:
    """心跳数据"""

    worker_id: str
    status: str
    metrics: Metrics
    os_info: OSInfo
    timestamp: datetime
    name: str = ""
    host: str = ""
    port: int = 0
    region: str = ""
    capabilities: dict = field(default_factory=dict)
    version: str = ""


class CapabilityDetector:
    """
    Worker能力检测器

    检测本地环境的渲染能力并上报给主控。
    """

    def __init__(self):
        self._cached_capabilities: dict | None = None
        self._platform = platform.system().lower()

    def detect_all(self, force_refresh: bool = False) -> dict:
        """检测所有能力"""
        if self._cached_capabilities and not force_refresh:
            return self._cached_capabilities

        capabilities = {
            "drissionpage": self._detect_drissionpage(),
            "curl_cffi": self._detect_curl_cffi(),
        }

        self._cached_capabilities = capabilities
        logger.debug(f"Worker能力检测完成: {self._summarize(capabilities)}")
        return capabilities

    def _summarize(self, capabilities: dict) -> str:
        """生成能力摘要"""
        enabled = []
        for name, cap in capabilities.items():
            if cap and cap.get("enabled"):
                extra = ""
                if name in ("drissionpage", "playwright", "selenium"):
                    headless = cap.get("headless", True)
                    extra = " (headless)" if headless else " (GUI)"
                enabled.append(f"{name}{extra}")
        return ", ".join(enabled) if enabled else "无渲染能力"

    def _get_default_headless(self) -> bool:
        """根据平台获取默认的 headless 设置"""
        if self._platform == "linux" and not os.getenv("DISPLAY"):
            return True
        return True

    def _detect_drissionpage(self) -> dict:
        """检测 DrissionPage 能力"""
        headless = self._get_default_headless()

        result = {
            "enabled": False,
            "browser_path": None,
            "headless": headless,
            "headless_forced": self._platform == "linux" and not os.getenv("DISPLAY"),
            "platform": self._platform,
        }

        try:
            from DrissionPage import ChromiumOptions  # noqa: F401
        except ImportError:
            result["error"] = "DrissionPage 未安装"
            return result

        browser_path = self._find_browser()
        if not browser_path:
            result["error"] = "未找到 Chrome/Chromium 浏览器"
            return result

        result["browser_path"] = browser_path
        result["enabled"] = True
        return result

    def _detect_curl_cffi(self) -> dict:
        """检测 curl_cffi 能力"""
        result = {"enabled": False}

        try:
            from curl_cffi import requests as curl_requests  # noqa: F401

            result["enabled"] = True
        except ImportError:
            pass

        return result

    def _find_browser(self) -> str | None:
        """查找 Chrome/Chromium 浏览器路径"""
        env_path = os.getenv("DRISSIONPAGE_BROWSER_PATH")
        if env_path and os.path.isfile(env_path):
            return env_path

        browser_paths = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/snap/bin/chromium",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "chrome",
            "chromium",
            "google-chrome",
            "google-chrome-stable",
        ]

        for path in browser_paths:
            if path.startswith("/"):
                if os.path.isfile(path):
                    return path
            else:
                found = shutil.which(path)
                if found:
                    return found

        return None

    def has_render_capability(self) -> bool:
        """检查是否有渲染能力"""
        caps = self.detect_all()
        return caps.get("drissionpage", {}).get("enabled", False)


# 全局能力检测器实例
_capability_detector: CapabilityDetector | None = None


def get_capability_detector() -> CapabilityDetector:
    """获取全局能力检测器"""
    global _capability_detector
    if _capability_detector is None:
        _capability_detector = CapabilityDetector()
    return _capability_detector


class HeartbeatReporter:
    """
    心跳上报器

    定期发送心跳维护与 Gateway/Redis 的连接状态。
    支持连续失败触发重连和降级模式。

    Requirements: 10.1, 10.3
    """

    MIN_INTERVAL = 1
    MAX_INTERVAL = 60
    DEFAULT_INTERVAL = 30
    MAX_CONSECUTIVE_FAILURES = 5
    DEGRADED_INTERVAL = 60          # 降级模式下的心跳间隔
    RECONNECT_BACKOFF_BASE = 2.0    # 重连退避基数
    RECONNECT_BACKOFF_MAX = 300.0   # 最大重连退避时间（秒）

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
    ):
        self._transport = transport
        self._worker_id = worker_id
        self._interval = self.DEFAULT_INTERVAL
        self._base_interval = self.DEFAULT_INTERVAL
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
    def interval(self) -> int:
        """心跳间隔"""
        return self._interval

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

    def update_worker_id(self, worker_id: str) -> None:
        """更新 Worker ID"""
        old_id = self._worker_id
        self._worker_id = worker_id
        logger.info(f"心跳上报器 worker_id 已更新: {old_id} -> {worker_id}")

    async def start(self, interval: int = 30) -> None:
        """启动心跳上报"""
        if self._running:
            return

        self._base_interval = max(self.MIN_INTERVAL, min(interval, self.MAX_INTERVAL))
        self._interval = self._base_interval
        self._running = True
        self._state = HeartbeatState.RUNNING
        self._task = asyncio.create_task(self._loop())
        logger.info(f"心跳上报已启动: interval={self._interval}s")

    async def stop(self) -> None:
        """停止心跳上报"""
        self._running = False
        self._state = HeartbeatState.STOPPED

        # 取消心跳任务
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

        # 取消重连任务
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
        self._reconnect_task = None

        logger.info("心跳上报已停止")

    async def send_heartbeat(self) -> bool:
        """发送心跳"""
        if not self._transport.is_connected:
            logger.debug("传输层未连接，跳过心跳")
            return False

        try:
            heartbeat = self._build_heartbeat()
            start = time.time()

            success = await self._transport.send_heartbeat(heartbeat)

            latency = (time.time() - start) * 1000

            if success:
                self._last_heartbeat_time = time.time()
                self._consecutive_failures = 0
                self._reconnect_attempts = 0
                self._adjust_interval(True)

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
                self._consecutive_failures += 1
                self._adjust_interval(False)
                logger.warning(f"心跳发送失败: consecutive={self._consecutive_failures}")
                return False

        except Exception as e:
            self._consecutive_failures += 1
            self._adjust_interval(False)
            logger.warning(f"心跳发送异常: {e}")
            return False

    async def _loop(self) -> None:
        """心跳循环"""
        while self._running:
            try:
                await self.send_heartbeat()

                # 检查是否需要触发重连
                if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                    await self._handle_consecutive_failures()

                # 根据状态选择间隔
                current_interval = self._get_current_interval()
                await asyncio.sleep(current_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳循环异常: {e}")
                await asyncio.sleep(5)

    def _get_current_interval(self) -> int:
        """获取当前心跳间隔"""
        if self._state == HeartbeatState.DEGRADED:
            return self.DEGRADED_INTERVAL
        return self._interval

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
        """
        尝试重连

        使用指数退避策略。

        Returns:
            是否重连成功
        """
        self._state = HeartbeatState.RECONNECTING
        self._reconnect_attempts += 1

        # 计算退避时间
        backoff = min(
            self.RECONNECT_BACKOFF_BASE ** self._reconnect_attempts,
            self.RECONNECT_BACKOFF_MAX,
        )

        logger.info(f"尝试重连 (attempt={self._reconnect_attempts}, backoff={backoff:.1f}s)")

        # 等待退避时间
        await asyncio.sleep(backoff)

        if not self._running:
            return False

        # 尝试重连
        try:
            if hasattr(self._transport, "reconnect"):
                success = await self._transport.reconnect()
            else:
                # 如果传输层没有 reconnect 方法，检查连接状态
                success = self._transport.is_connected

            if success:
                logger.info(f"重连成功 (attempts={self._reconnect_attempts})")
                self._state = HeartbeatState.RUNNING
                self._reconnect_attempts = 0
                self._last_reconnect_time = time.time()

                # 触发重连成功回调
                if self._on_reconnect:
                    try:
                        result = self._on_reconnect()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"重连成功回调异常: {e}")

                return True
            else:
                logger.warning(f"重连失败 (attempts={self._reconnect_attempts})")
                self._state = HeartbeatState.DEGRADED
                return False

        except Exception as e:
            logger.error(f"重连异常: {e}")
            self._state = HeartbeatState.DEGRADED
            return False

    def _adjust_interval(self, success: bool) -> None:
        """调整心跳间隔"""
        old = self._interval
        if success:
            self._interval = self._base_interval
        else:
            self._interval = self.MIN_INTERVAL

        if self._interval != old:
            logger.debug(f"心跳间隔调整: {old}s -> {self._interval}s")

    def _build_heartbeat(self) -> Heartbeat:
        """构建心跳数据"""
        return Heartbeat(
            worker_id=self._worker_id,
            name=self._name,
            host=self._host,
            port=self._port,
            region=self._region,
            status="online",
            metrics=self._get_metrics(),
            os_info=self._get_os_info(),
            timestamp=datetime.now(),
            capabilities=self._get_capabilities(),
            version=self._version,
        )

    def _get_metrics(self) -> Metrics:
        """获取系统指标"""
        try:
            if self._metrics_collector:
                m = self._metrics_collector.get_metrics()
                spider_stats = None

                stats = self._metrics_collector.get_spider_stats()
                if stats:
                    spider_stats = SpiderStats(
                        request_count=stats.get("request_count", 0),
                        response_count=stats.get("response_count", 0),
                        item_scraped_count=stats.get("item_scraped_count", 0),
                        error_count=stats.get("error_count", 0),
                        avg_latency_ms=stats.get("avg_latency_ms", 0.0),
                        requests_per_minute=stats.get("requests_per_minute", 0.0),
                        status_codes=stats.get("status_codes", {}),
                    )

                return Metrics(
                    cpu=round(max(0.0, min(100.0, m.get("cpu", 0.0))), 1),
                    memory=round(max(0.0, min(100.0, m.get("memory", 0.0))), 1),
                    disk=round(max(0.0, min(100.0, m.get("disk", 0.0))), 1),
                    running_tasks=m.get("runningTasks", 0),
                    max_concurrent_tasks=m.get(
                        "maxConcurrentTasks", self._max_concurrent_tasks
                    ),
                    task_count=m.get("taskCount", 0),
                    project_count=m.get("projectCount", 0),
                    env_count=m.get("envCount", 0),
                    spider_stats=spider_stats,
                )

            # 默认指标
            return Metrics(max_concurrent_tasks=self._max_concurrent_tasks)

        except Exception as e:
            logger.warning(f"获取指标失败: {e}")
            return Metrics(max_concurrent_tasks=self._max_concurrent_tasks)

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
        try:
            detector = get_capability_detector()
            return detector.detect_all()
        except Exception:
            return {}


# 全局实例
_heartbeat_reporter: HeartbeatReporter | None = None


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
