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

### 调度循环 (Tick Loop)
Master 内部运行着一个高频的事件循环 (Tick)，每秒钟会执行一次检查：
-   检查是否有新任务到达预定时间。
-   检查是否有正在运行的任务超时。
-   检查是否有 Worker 心跳超时。

### 队列管理
Master 使用 Redis Stream 作为消息队列，确保消息的持久化与顺序性。
-   **Stream Key**: `antcode:tasks:stream`
-   **Group Name**: `antcode:workers`
