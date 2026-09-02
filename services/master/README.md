# 🧠 AntCode Master (调度与协调)

Master 是 AntCode 的"心脏"，负责整个系统的任务调度、故障恢复与数据一致性维护。它不直接执行任务，而是确保任务被正确地分发给合适的 Worker。

---

## 🎯 核心职责

1.  **任务分发 (Distribution)**: 扫描待执行的任务，根据路由策略将其投递到 Redis Stream。
2.  **故障恢复 (Recovery)**: 监控任务执行状态，自动重试失败任务，处理 Worker 宕机导致的僵尸任务。
3.  **定时调度 (Scheduler)**: 解析 Cron 表达式，定时触发周期性任务。
4.  **状态同步 (Sync)**: 维护任务状态机，确保数据库与 Redis 队列状态一致。

---

## ⚡ 快速启动

### 命令行启动

```bash
uv run python -m antcode_master
```

### 推荐配置

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DATABASE_URL` | - | 数据库连接串 (必须) |
| `REDIS_URL` | - | Redis 连接串 (必须) |
| `LOG_LEVEL` | `INFO` | 日志级别 |

---

## ⚙️ 内部机制

### 后台 loop
没有统一的 tick；12 个 loop 各有自己的节拍，分 control / ingester 两组并行启动
（见 `antcode_master/__main__.py`）。几个关键节拍：

-   `lease_sweeper` 1s —— 扫过期 lease，触发失租剔除（Worker 存活判定走这里，不是心跳轮询）
-   `reconcile` 60s —— 任务状态收敛与超时判定
-   `scheduler` / `scheduler_event` / `crawl_batch_status` / `artifact_cleanup` leader poll 30s
-   `result` / `log_ingest` 1s，走 XREADGROUP consumer name 天然分区，可分片到多实例；
    其余 loop leader-only

### 队列管理
Redis Stream，key 命名的权威定义在
`antcode_core.infrastructure.redis.control_plane`：

-   **任务 ready stream**: `{<ns>}:task:ready:<worker_id>`（每 Worker 一条，与 lease 同 hash slot）
-   **结果 stream**: `<ns>:task:result`
-   **消费组**: `<ns>-workers`（连字符，不是冒号）

`<ns>` 即 `REDIS_NAMESPACE`，默认 `antcode`。
