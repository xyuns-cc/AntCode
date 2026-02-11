# 系统配置管理

## 适用范围

本文档描述 `web_api` 管理的系统配置项与生效策略，面向管理员与运维。

## 权限模型

- 仅超级管理员可修改系统配置
- 配置变更需记录审计日志

## 常见配置分类

### 调度与执行

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `MAX_CONCURRENT_TASKS` | `10` | 系统级并发上限 |
| `TASK_EXECUTION_TIMEOUT` | `3600` | 单任务超时（秒） |
| `TASK_MAX_RETRIES` | `3` | 最大重试次数 |
| `TASK_RETRY_DELAY` | `60` | 重试间隔（秒） |

### 日志与归档

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `TASK_LOG_RETENTION_DAYS` | `30` | 任务日志保留天数 |
| `LOG_STORAGE_BACKEND` | `s3` | 日志存储后端（`s3/local/clickhouse`） |
| `LOG_ARCHIVE_RETENTION_DAYS` | `30` | 归档保留天数 |

### 缓存与监控

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `CACHE_ENABLED` | `true` | 是否启用缓存 |
| `CACHE_DEFAULT_TTL` | `300` | 默认缓存 TTL（秒） |
| `MONITORING_ENABLED` | `true` | 是否启用监控 |
| `MONITOR_HISTORY_KEEP_DAYS` | `30` | 监控历史保留天数 |

## 生效策略

### 热更新即可生效（多数场景）

- 日志保留、缓存 TTL、部分监控参数
- 与请求处理直接相关但不依赖初始化阶段的配置

### 需要重启服务

- 线程池大小、调度器初始化参数
- 依赖启动时一次性加载的资源配置

## API（管理端）

```http
GET  /api/v1/system-config/
GET  /api/v1/system-config/by-category
PUT  /api/v1/system-config/{config_key}
POST /api/v1/system-config/batch
POST /api/v1/system-config/reload
```

## 路径规范

- 后端运行时目录：`data/backend`
- Worker 运行时目录：`data/worker`
- 配置项与文档中不再使用历史目录命名
