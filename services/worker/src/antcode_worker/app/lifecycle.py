"""
生命周期管理

负责 Worker 的启动和关闭流程。

Requirements: 2.5
"""

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger

from antcode_worker.transport.base import WorkerState

class Lifecycle:
    """
    生命周期管理器

    管理 Worker 组件的启动和关闭顺序。

    Requirements: 2.5
    """

    def __init__(self):
        self._startup_hooks: list[Callable] = []
        self._shutdown_hooks: list[Callable] = []
        self._running = False
        self._shutdown_event: asyncio.Event | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def on_startup(self, hook: Callable) -> None:
        """注册启动钩子"""
        self._startup_hooks.append(hook)

    def on_shutdown(self, hook: Callable) -> None:
        """注册关闭钩子（后注册先执行）"""
        self._shutdown_hooks.insert(0, hook)

    async def startup(self, container: Any) -> None:
        """
        执行启动流程

        启动顺序：
        1. Transport（优先启动）
        2. RuntimeManager
        3. Executor
        4. ObservabilityServer
        5. HeartbeatReporter
        6. Engine
        7. 自定义钩子
        """
        logger.info("开始启动 Worker...")
        self._shutdown_event = asyncio.Event()

        try:
            self._bind_transport_state(container)

            # 启动传输层
            if container.transport:
                transport_started = await container.transport.start()
                if transport_started:
                    logger.info("传输层已启动")
                else:
                    logger.warning("传输层启动失败，进入降级模式，将自动重连")

            # 启动运行时管理器
            if container.runtime_manager:
                await container.runtime_manager.start()
                logger.info("运行时管理器已启动")

            # 启动日志清理
            if container.log_cleanup:
                await container.log_cleanup.start()
                logger.info("日志清理服务已启动")

            # 启动执行器
            if container.executor:
                await container.executor.start()
                logger.info("执行器已启动")

            # 启动可观测性服务器
            if container.observability_server:
                host = getattr(container.config, "host", "0.0.0.0")
                port = getattr(container.config, "port", 8001)
                await container.observability_server.start(host=host, port=port)

            # 启动心跳
            if container.heartbeat_reporter:
                interval = getattr(container.config, "heartbeat_interval", 30)
                await container.heartbeat_reporter.start(interval=interval)
                logger.info("心跳上报已启动")

            # 启动引擎
            if container.engine:
                await container.engine.start()
                logger.info("引擎已启动")

            # 设置就绪
            if container.observability_server:
                is_ready = True
                if container.transport:
                    is_ready = container.transport.is_connected
                container.observability_server.set_ready(is_ready)

            # 执行自定义启动钩子
            for hook in self._startup_hooks:
                result = hook()
                if asyncio.iscoroutine(result):
                    await result

            self._running = True
            logger.info("Worker 启动完成")

        except Exception as e:
            logger.error(f"启动失败: {e}")
            await self.shutdown(container)
            raise

    def _bind_transport_state(self, container: Any) -> None:
        """绑定传输层状态变更回调"""
        if not container or not container.transport:
            return

        async def _on_state_change(old_state: WorkerState, new_state: WorkerState) -> None:
            if container.observability_server:
                container.observability_server.set_ready(new_state == WorkerState.ONLINE)

            if new_state == WorkerState.ONLINE:
                logger.info("传输层已恢复在线")
            elif old_state == WorkerState.ONLINE and new_state != WorkerState.ONLINE:
                logger.warning("传输层离线，进入降级模式")

        container.transport.on_state_change(_on_state_change)

    async def shutdown(self, container: Any, grace_period: float = 30.0) -> None:
        """
        执行关闭流程

        关闭顺序（与启动相反）：
        1. 停止接收新任务（Engine.stop_polling）
        2. 等待运行中任务完成（最长 grace_period）
        3. 强制终止未完成任务
        4. 停止心跳
        5. 停止执行器
        6. 停止运行时管理器
        7. 停止可观测性服务器
        8. 停止传输层
        6. 自定义钩子
        """
        if not self._running:
            return

        logger.info(f"开始关闭 Worker (grace_period={grace_period}s)...")
        self._running = False

        try:
            # 执行自定义关闭钩子
            for hook in self._shutdown_hooks:
                try:
                    result = hook()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.warning(f"关闭钩子执行失败: {e}")

            # 停止引擎（会 drain 任务）
            if container.engine:
                await container.engine.stop(grace_period=grace_period)
                logger.info("引擎已停止")

            # 停止心跳
            if container.heartbeat_reporter:
                await container.heartbeat_reporter.stop()
                logger.info("心跳上报已停止")

            # 停止执行器
            if container.executor:
                await container.executor.stop()
                logger.info("执行器已停止")

            # 停止日志清理
            if container.log_cleanup:
                await container.log_cleanup.stop()
                logger.info("日志清理服务已停止")

            # 停止运行时管理器
            if container.runtime_manager:
                await container.runtime_manager.stop()
                logger.info("运行时管理器已停止")

            # 停止可观测性服务器
            if container.observability_server:
                await container.observability_server.stop()
                logger.info("可观测性服务已停止")

            # 停止传输层
            if container.transport:
                await container.transport.stop(grace_period=5.0)
                logger.info("传输层已停止")

            logger.info("Worker 已关闭")

        except Exception as e:
            logger.error(f"关闭过程异常: {e}")
        finally:
            if self._shutdown_event:
                self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        """等待关闭信号"""
        if self._shutdown_event:
            await self._shutdown_event.wait()

    def trigger_shutdown(self) -> None:
        """触发关闭"""
        if self._shutdown_event:
            self._shutdown_event.set()
