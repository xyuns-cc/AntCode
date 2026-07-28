# Worker 传输模式指南

## 概述

Worker 仅通过以下两种方式接入系统：

- **Direct 模式**：内网直连 Redis Streams
- **Gateway 模式**：公网通过 gRPC Gateway 接入

两种模式统一遵循 `poll -> execute -> report -> ack` 语义。

## Direct 模式

> **部署边界：Direct 仅用于可信内网、单租户测试环境，不是生产接入模式。**
> 当前 task Stream 与 Lease key 不在同一个 Redis Cluster slot，任务 ACK / requeue
> 无法在一个 Lua 内同时校验 Lease；Spider/run key 也未携带 `worker_id`，Redis ACL
> 无法表达逐 Worker 的 run ownership。ACL 泄漏或代际切换窗口内均存在跨边界写入风险。

### 场景

- Worker 与 Redis 网络互通
- 对链路延迟敏感的可信单租户测试部署

### 关键配置

- `WORKER_TRANSPORT_MODE=direct`
- `WORKER_REDIS_URL`（或 `REDIS_URL`）
- `WORKER_REDIS_NAMESPACE`（可选，默认 `antcode`）

## Gateway 模式

Gateway 是生产环境唯一受支持的 Worker 接入模式。生产 Worker 不得获得
PostgreSQL 或 Redis 凭据；`WORKER_REDIS_URL`、`DATABASE_URL`、`REDIS_URL`
必须为空，并显式设置 `WORKER_GATEWAY_BACKENDLESS=true`。该开关只允许与
`WORKER_TRANSPORT_MODE=gateway` 组合，检测到任何后端 URL 会拒绝启动。

### 场景

- 公网 Worker 或跨网络部署
- 不希望暴露 Redis / PostgreSQL 给 Worker

### 关键配置

- `WORKER_TRANSPORT_MODE=gateway`
- `WORKER_GATEWAY_BACKENDLESS=true`
- `WORKER_GATEWAY_ENDPOINT`（或 `WORKER_GATEWAY_HOST` + `WORKER_GATEWAY_PORT`）
- TLS：`WORKER_GATEWAY_TLS=true`（结合证书配置）

## 可靠性机制

- Redis 消费组 `XREADGROUP + XACK + XAUTOCLAIM`
- 幂等结果上报，避免重复终态写入
- 网络断连自动重连与退避

### 升级注意：run ownership 键布局变更

ownership 键从 `<ns>:run:owner:<run_id>` 迁移为带 hash tag 的
`{<ns>}:run:owner:<run_id>`（与 Lease key 同 slot，Lua 原子校验的前提），
且不做数据迁移。ownership TTL 约为 65 分钟（lease TTL + margin），滚动
升级期间旧进程持有的旧键对新进程不可见，run 互斥在该窗口内退化。

- **推荐**：升级前 drain —— 暂停派发新 run，等在途 run 结束后再滚动
  重启 Gateway/Worker，窗口完全消除。
- **可接受**：直接滚动升级，接受旧键在 TTL 内自然过期；期间同一 run
  被新旧两代进程同时持有的概率窗口以 TTL 为上界。

## 安全建议

- Gateway 模式优先启用 API Key 或 mTLS
- 将证书/密钥放置于 `data/worker/secrets`
- Worker 最小权限运行，不持有 PostgreSQL / Redis 凭据

## 不再支持

- Master 反连 Worker
- gRPC/HTTP 回退混合链路
