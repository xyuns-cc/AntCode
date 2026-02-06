"""
应用层

负责组装和启动 Worker 应用。
"""

from antcode_worker.app.lifecycle import Lifecycle
from antcode_worker.app.main import Application
from antcode_worker.app.wiring import Container

__all__ = ["Application", "Container", "Lifecycle"]
