"""
AntCode Worker 执行器服务

Execution Plane 的核心组件，负责：
- 任务执行
- 运行时管理（uv 环境）
- 日志输出（实时流 + 归档）
- 心跳上报

支持两种传输模式：
- Direct 模式：内网直连 Redis Streams
- Gateway 模式：公网通过 Gateway gRPC/TLS 连接
"""

__version__ = "0.1.0"

# 导出子模块
from antcode_worker import executor, heartbeat, logging, runtime, transport

__all__ = [
    "__version__",
    # 子模块
    "transport",
    "runtime",
    "executor",
    "logging",
    "heartbeat",
]
