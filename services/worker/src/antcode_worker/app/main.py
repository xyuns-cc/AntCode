"""
应用主入口

负责组装和启动 Worker 应用。

Requirements: 2.3
"""

import asyncio
import os
import signal
import sys
from typing import Any

from loguru import logger

from antcode_worker.app.lifecycle import Lifecycle
from antcode_worker.app.wiring import Container, create_container


class GracefulShutdown:
    """优雅关闭管理器

    特性：
    - 第一次信号：触发优雅关闭
    - 第二次信号：强制退出
    - 超时保护：防止关闭过程卡死
    """

    def __init__(self, grace_period: float = 30.0):
        self._grace_period = grace_period
        self._signal_count = 0
        self._shutdown_event = asyncio.Event()
        self._handlers_installed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._force_exit_handle: asyncio.Handle | None = None

    @property
    def is_shutting_down(self) -> bool:
        return self._shutdown_event.is_set()

    def install_handlers(self) -> None:
        """安装信号处理器"""
        if self._handlers_installed:
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        def _signal_handler(signum: int, frame: Any) -> None:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._handle_signal, signum, frame)
            else:
                self._handle_signal(signum, frame)

        if sys.platform == "win32":
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)
        else:
            installed = False
            if self._loop:
                try:
                    for sig in (signal.SIGTERM, signal.SIGINT):
                        self._loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s, None))
                    installed = True
                except (RuntimeError, ValueError, NotImplementedError):
                    installed = False

            if not installed:
                signal.signal(signal.SIGINT, _signal_handler)
                signal.signal(signal.SIGTERM, _signal_handler)

        self._handlers_installed = True

    def _handle_signal(self, signum: int | signal.Signals, frame: Any) -> None:
        """信号处理"""
        self._signal_count += 1
        sig_name = signal.Signals(signum).name if isinstance(signum, int) else signum.name

        if self._signal_count == 1:
            logger.info(f"收到 {sig_name}，开始优雅关闭... (再次发送信号强制退出)")
            self._shutdown_event.set()
            self._schedule_force_exit(signum)
        elif self._signal_count == 2:
            logger.warning(f"收到第二次 {sig_name}，强制退出")
            os._exit(128 + signum if isinstance(signum, int) else 1)
        else:
            os._exit(1)

    def _schedule_force_exit(self, signum: int | signal.Signals) -> None:
        if self._force_exit_handle or not self._loop or not self._loop.is_running():
            return

        delay = self._grace_period + 5
        self._force_exit_handle = self._loop.call_later(
            delay,
            lambda: self._force_exit(signum),
        )

    def _force_exit(self, signum: int | signal.Signals) -> None:
        self._force_exit_handle = None
        logger.warning("优雅关闭超时，强制退出")
        os._exit(128 + int(signum) if isinstance(signum, int) else 1)

    def cancel_force_exit(self) -> None:
        if self._force_exit_handle:
            self._force_exit_handle.cancel()
            self._force_exit_handle = None

    async def wait(self) -> None:
        """等待关闭信号"""
        await self._shutdown_event.wait()

    def trigger(self) -> None:
        """手动触发关闭"""
        self._shutdown_event.set()


class Application:
    """
    Worker 应用

    Requirements: 2.3
    """

    def __init__(self, config: Any):
        self.config = config
        self.container: Container | None = None
        self.lifecycle = Lifecycle()
        self._graceful = GracefulShutdown(grace_period=getattr(config, "grace_period", 30.0))

    async def setup(self) -> None:
        """初始化应用"""
        logger.info("初始化 Worker 应用...")
        self.container = create_container(self.config)

    async def run(self) -> None:
        """运行应用"""
        if not self.container:
            await self.setup()

        # 安装信号处理
        self._graceful.install_handlers()

        # 启动服务
        await self.lifecycle.startup(self.container)
        self._log_status()

        # 等待关闭信号
        await self._graceful.wait()

        # 执行关闭
        await self._shutdown_with_timeout()

    async def _shutdown_with_timeout(self) -> None:
        """带超时的关闭流程"""
        grace_period = getattr(self.config, "grace_period", 30.0)

        try:
            async with asyncio.timeout(grace_period + 5):
                if self.container:
                    await self.lifecycle.shutdown(self.container, grace_period)
        except TimeoutError:
            logger.warning(f"关闭超时 ({grace_period + 5}s)，部分资源可能未正确释放")
        finally:
            self._graceful.cancel_force_exit()

    def _log_status(self) -> None:
        """输出运行状态"""
        name = getattr(self.config, "name", "Worker")
        transport_mode = getattr(self.config, "transport_mode", "gateway")
        port = getattr(self.config, "port", 8001)
        host = getattr(self.config, "host", "0.0.0.0")
        max_concurrent = getattr(self.config, "max_concurrent_tasks", 5)
        health_url = f"http://{host}:{port}/health"
        logger.info(
            "Worker 已启动: name={} transport_mode={} health_url={} max_concurrent={}",
            name,
            transport_mode,
            health_url,
            max_concurrent,
        )


async def run_worker(config: Any) -> None:
    """运行 Worker"""
    app = Application(config)
    await app.run()
