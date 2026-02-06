"""
AntCode Gateway Service - Data Plane

gRPC 网关服务，负责：
- 公网 Worker 节点的 gRPC/TLS 通信
- 认证与授权（mTLS/API Key/JWT）
- 请求限流与熔断
- 代理 Worker poll 任务（从 Redis Streams 读取）
- 接收日志写入 log:{run_id} stream
- 接收结果并回写 MySQL

职责边界：
- 不实现复杂调度策略（只代理队列 + 写状态/日志/结果）
- 不处理业务 CRUD
"""

__version__ = "0.1.0"

from antcode_gateway.auth import AuthInterceptor
from antcode_gateway.rate_limit import RateLimiter, RateLimitInterceptor
from antcode_gateway.server import GrpcServer, get_grpc_server

__all__ = [
    "__version__",
    "GrpcServer",
    "get_grpc_server",
    "AuthInterceptor",
    "RateLimiter",
    "RateLimitInterceptor",
]
