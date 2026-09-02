"""
gRPC 处理器模块

实现各种 gRPC 请求的处理逻辑：
- poll: 从 Redis Streams 读取任务，转码为 ``TaskDispatch`` 推送给 Worker
- heartbeat: 处理 ``ControlService.Lease`` 上报，写入 Redis worker 心跳 Hash
- logs: 接收 ``LogBatch`` Proto，整批写入全局 ``<namespace>:log:ingest`` stream
- result: 接收 ``TaskStatus`` Proto，写入 task_result_stream

**Validates: Requirements 6.3, 6.5, 6.6**
"""

from antcode_gateway.handlers.heartbeat import (
    HeartbeatData,
    HeartbeatHandler,
    LeaseData,
    LeaseHandler,
)
from antcode_gateway.handlers.logs import LogHandler
from antcode_gateway.handlers.poll import TaskPollHandler, task_info_to_dispatch
from antcode_gateway.handlers.result import ResultHandler
from antcode_gateway.handlers.spider_data import SpiderDataHandler

__all__ = [
    "TaskPollHandler",
    "task_info_to_dispatch",
    "HeartbeatHandler",
    "HeartbeatData",
    "LeaseHandler",
    "LeaseData",
    "LogHandler",
    "ResultHandler",
    "SpiderDataHandler",
]
