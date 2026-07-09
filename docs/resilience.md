# 弹性与容错

## 目标

在节点抖动、网络中断、依赖超时等场景下，保证任务系统可恢复、可观测、可追踪。

## 机制总览

| 机制 | 位置 | 作用 |
|---|---|---|
| 重试与补偿 | `services/master/src/antcode_master/loops/` | 对失败任务进行重试与状态修复 |
| 连接恢复 | `services/worker/src/antcode_worker/transport/` | Worker 断线自动重连与退避 |
| 健康检查 | `services/web_api/src/antcode_web_api/routes/v1/health.py` | 对外暴露系统健康状态 |
| 限流与保护 | `services/gateway/src/antcode_gateway/rate_limit.py` | 防止公网入口过载 |
| 日志传输 | Redis Stream + PostgreSQL `task_logs` | Redis 负责实时热流，PostgreSQL 负责历史持久化 |

## 关键策略

### 1) 任务交付可靠性

- 使用 Redis Stream 消费组语义（ACK + 回收）
- Worker 结果上报按幂等策略处理
- Master 定期巡检并做补偿修复

### 2) 网络故障恢复

- Worker 与 Gateway/Redis 连接断开后自动重连
- 指数退避避免雪崩重试
- 网关侧限流保护后端依赖

### 3) 日志可靠传输

- 实时流：Worker 写入 Redis Stream，WebSocket 只跟随 Redis 热流
- 历史日志：Master ingest 消费 Redis 日志队列并写入 PostgreSQL
- 失败暴露：Redis 写入、ACK、PostgreSQL 落库失败必须显式失败

## 健康检查建议

- Liveness：进程是否存活
- Readiness：依赖（PostgreSQL/Redis）是否可用
- 对外统一通过 `web_api` 健康接口暴露

## 运维实践

1. 重试参数分环境配置（开发与生产分离）
2. 监控重试率、补偿率、日志堆积量
3. 发生故障时优先保留 PostgreSQL `task_logs` 与 Redis 控制流现场
4. 通过审计日志追踪配置变更与恢复动作
