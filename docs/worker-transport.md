# Worker 传输模式指南

## 概述

Worker 仅通过以下两种方式接入系统：

- **Direct 模式**：内网直连 Redis Streams
- **Gateway 模式**：公网通过 gRPC Gateway 接入

两种模式统一遵循 `poll -> execute -> report -> ack` 语义。

## Direct 模式

### 场景

- Worker 与 Redis 网络互通
- 对链路延迟敏感的内网部署

### 关键配置

- `WORKER_TRANSPORT_MODE=direct`
- `WORKER_REDIS_URL`（或 `REDIS_URL`）
- `WORKER_REDIS_NAMESPACE`（可选，默认 `antcode`）

## Gateway 模式

### 场景

- 公网 Worker 或跨网络部署
- 不希望暴露 Redis / MySQL 给 Worker

### 关键配置

- `WORKER_TRANSPORT_MODE=gateway`
- `WORKER_GATEWAY_ENDPOINT`（或 `WORKER_GATEWAY_HOST` + `WORKER_GATEWAY_PORT`）
- TLS：`WORKER_GATEWAY_TLS=true`（结合证书配置）

## 可靠性机制

- Redis 消费组 `XREADGROUP + XACK + XAUTOCLAIM`
- 幂等结果上报，避免重复终态写入
- 网络断连自动重连与退避

## 安全建议

- Gateway 模式优先启用 API Key 或 mTLS
- 将证书/密钥放置于 `data/worker/secrets`
- Worker 最小权限运行，不直接持有后端数据库凭据

## 不再支持

- Master 反连 Worker
- gRPC/HTTP 回退混合链路
