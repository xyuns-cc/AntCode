# AntCode Gateway（Data Plane）

Gateway 为公网 Worker 提供统一 gRPC 接入层，负责认证、限流、协议转换与后端交互。

## 核心职责

- 暴露 gRPC 服务供 Worker 接入
- 执行 API Key / mTLS 认证
- 承担限流、连接治理与防护
- 将 Worker 请求转换为后端可消费的数据流

## 目录结构

```text
services/gateway/
├── src/antcode_gateway/
│   ├── auth.py
│   ├── rate_limit.py
│   ├── server.py
│   ├── config.py
│   ├── handlers/
│   └── services/
└── pyproject.toml
```

## 启动

```bash
uv run python -m antcode_gateway
```

## 关键配置

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `GRPC_HOST` | `0.0.0.0` | gRPC 监听地址 |
| `GRPC_PORT` | `50051` | gRPC 监听端口 |
| `GRPC_MAX_WORKERS` | `10` | gRPC 工作线程 |
| `AUTH_ENABLED` | `true` | 认证开关 |
| `RATE_LIMIT_ENABLED` | `true` | 限流开关 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接 |

TLS（可选）：

- `GRPC_TLS_CERT_PATH`
- `GRPC_TLS_KEY_PATH`
- `GRPC_TLS_CA_PATH`（启用 mTLS 时必需）

## 运行时目录

Gateway 归属后端运行时目录：`data/backend`。

## 边界说明

- Gateway 不实现调度策略（由 `master` 负责）
- Gateway 不提供业务 CRUD（由 `web_api` 负责）
