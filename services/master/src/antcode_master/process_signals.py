"""Process signal registration for the Master entry point."""

from __future__ import annotations

import asyncio
import signal
import sys

from loguru import logger


def install_stop_signal_handlers() -> asyncio.Event:
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()
    stopping = False

    def request_stop() -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        logger.info("收到停止信号")
        stop_event.set()

    if sys.platform == "win32":

        def windows_handler(signum, frame):  # noqa: ARG001
            loop.call_soon_threadsafe(request_stop)

        for target_signal in (signal.SIGTERM, signal.SIGINT):
            signal.signal(target_signal, windows_handler)
    else:
        for target_signal in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(target_signal, request_stop)
    return stop_event


__all__ = ["install_stop_signal_handlers"]
