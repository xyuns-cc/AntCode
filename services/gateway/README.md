# AntCode Gateway Service - Data Plane

gRPC 网关服务，负责公网 Worker 节点的通信。

## 功能

- **gRPC 服务**: 实现 GatewayService
- **认证授权**: 支持 mTLS、API Key、JWT
- **限流熔断**: 令牌桶算法保护后端服务
- **任务代理**: 从 Redis Streams 读取任务并分发给 Worker
- **日志收集**: 接收 Worker 日志写入 Redis Streams
- **结果回写**: 接收 Worker 执行结果并回写 MySQL

## 架构

```
Worker (gRPC Client)
    ↓ gRPC/TLS/mTLS
Gateway (gRPC Server)
    ↓
Redis Streams ← Master (调度器)
    ↓
MySQL (状态持久化)
```

## 职责边界

Gateway 只负责：
- 代理队列读取（不实现调度策略）
- 写状态/日志/结果
- 认证、限流、审计

Gateway 不负责：
- 复杂调度策略（由 Master 负责）
- 业务 CRUD（由 Web API 负责）

## 依赖

- `antcode_core`: 共享核心代码
- `antcode_contracts`: gRPC 契约定义
- `grpcio`: gRPC 框架
- `redis`: Redis 客户端

## 目录结构

```
services/gateway/
├── pyproject.toml
├── README.md
└── src/
    └── antcode_gateway/
        ├── __init__.py
        ├── __main__.py      # 模块入口
        ├── main.py          # 服务入口
        ├── server.py        # gRPC 服务器
        ├── config.py        # 配置
        ├── auth.py          # 认证拦截器
        ├── rate_limit.py    # 限流拦截器
        ├── handlers/        # 请求处理器
        │   ├── poll.py      # 任务轮询
        │   ├── heartbeat.py # 心跳处理
        │   ├── logs.py      # 日志处理
        │   └── result.py    # 结果处理
        └── services/        # gRPC 服务实现
            └── gateway_service.py
```

## 运行

```bash
# 开发模式
uv run python -m antcode_gateway

# 指定端口
uv run python -m antcode_gateway --host 0.0.0.0 --port 50051

# 调试模式
uv run python -m antcode_gateway --debug
```

## 配置

通过环境变量配置：

### 服务器配置
- `GRPC_HOST`: gRPC 监听地址（默认: 0.0.0.0）
- `GRPC_PORT`: gRPC 监听端口（默认: 50051）
- `GRPC_MAX_WORKERS`: 最大工作线程数（默认: 10）
- `GRPC_ENABLED`: 是否启用 gRPC 服务（默认: true）

### TLS 配置
- `GRPC_TLS_CERT_PATH`: TLS 证书路径
- `GRPC_TLS_KEY_PATH`: TLS 私钥路径
- `GRPC_TLS_CA_PATH`: CA 证书路径（mTLS）

### 认证配置
- `AUTH_ENABLED`: 是否启用认证（默认: true）

### 限流配置
- `RATE_LIMIT_ENABLED`: 是否启用限流（默认: true）
- `RATE_LIMIT_RATE`: 每秒请求数（默认: 100）
- `RATE_LIMIT_CAPACITY`: 令牌桶容量（默认: 200）

### Redis 配置
- `REDIS_URL`: Redis 连接 URL（默认: redis://localhost:6379/0）

## 认证方式

支持三种认证方式（按优先级）：

1. **API Key**: 通过 `x-api-key` 头传递
2. **JWT**: 通过 `Authorization: Bearer <token>` 头传递
3. **mTLS**: 通过客户端证书认证（需配置 TLS）

## 测试

```bash
# 运行单元测试
uv run pytest tests/unit/

# 运行集成测试
uv run pytest tests/integration/
```
