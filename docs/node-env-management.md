# Worker 环境与运行时管理

## 概述

虚拟环境与解释器由 Worker 负责实际执行，后端负责编排、状态记录与对外 API。

## 职责边界

| 角色 | 职责 |
|---|---|
| `web_api` / `master` | 接收请求、选择目标 Worker、记录元数据 |
| `worker` | 创建/清理运行时、安装依赖、执行任务 |

## 运行时目录

Worker 默认使用 `data/worker`：

```text
data/worker/
├── runtimes/     # Python 运行时缓存
├── projects/     # 项目缓存
├── runs/         # 运行产物
└── logs/         # 执行日志
```

## 典型流程

1. 后端根据策略选择 Worker
2. Worker 准备运行时与依赖
3. Worker 执行任务并持续上报日志
4. 结束后回写状态与结果

## 配置要点

| 变量 | 说明 |
|---|---|
| `WORKER_DATA_DIR` | Worker 数据根目录 |
| `WORKER_TRANSPORT_MODE` | `direct` 或 `gateway` |
| `WORKER_MAX_CONCURRENT_TASKS` | Worker 并发上限 |
| `TASK_EXECUTION_TIMEOUT` | 执行超时控制 |

## 运维建议

- 定期清理过期运行时与日志
- 将 `data/worker` 挂载到持久化磁盘
- 对关键 Worker 启用独立监控与告警
- 多 Worker 部署时保持 Python 版本策略一致
