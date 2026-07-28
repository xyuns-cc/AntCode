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

### 日志

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `TASK_LOG_RETENTION_DAYS` | `30` | 任务日志保留天数 |

### SpiderData 容量与保留

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `ANTCODE_SPIDER_STREAM_MAXLEN` | `0` | Worker Direct/legacy sink 的单 run Stream 上限；`0` 不裁剪 |
| `ANTCODE_SPIDER_META_TTL_SECONDS` | `0` | Worker Direct 的 stream/meta/marker TTL 与 index member 过期时间；`0` 不过期 |
| `SPIDER_STREAM_MAXLEN` | `0` | Gateway handler 的单 run Stream 上限；`0` 不裁剪 |
| `SPIDER_META_TTL_SECONDS` | `0` | Gateway 的 stream/meta/marker TTL 与 index member 过期时间；`0` 不过期 |

SpiderData 是任务结果而非预览缓存，因此默认不设置 Redis `MAXLEN` 或
`EXPIRE`。管理员必须结合 Redis 容量、备份和归档策略显式配置正整数才能
启用 retention；负数、浮点数或其他非法值会直接报错，不会回退到隐藏默认值。

### 缓存与监控

| 配置项 | 默认值 | 说明 |
|---|---:|---|
| `CACHE_ENABLED` | `true` | 是否启用缓存 |
| `CACHE_DEFAULT_TTL` | `300` | 默认缓存 TTL（秒） |
| `MONITORING_ENABLED` | `true` | 是否启用监控 |
| `MONITOR_HISTORY_KEEP_DAYS` | `30` | 监控历史保留天数 |

### Git 源码网络边界

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `GIT_MAX_TRANSFER_BYTES` | `536870912` | 单次源码解析、clone 与 checkout 经 pinned HTTP(S) proxy 或 SSH relay 实际转发的双向总字节上限；超限会断开连接并终止整个 Git 进程组 |

该上限由一次源码获取流程中的全部 `ls-remote`、`clone`、partial checkout 命令
及其并发连接共享。它不以服务端 partial-clone filter、命令输出
大小或仓库落盘大小代替网络计数。Git system/global config、hook、递归 submodule、
`file`/`ext` protocol 和 LFS smudge 均被禁用，避免启动不经过受控 relay 的网络进程；
checkout 后仍存在的 LFS pointer 会显式拒绝生成 source bundle。

## 生效策略

### 热更新即可生效（多数场景）

- 日志保留、缓存 TTL、部分监控参数
- 与请求处理直接相关但不依赖初始化阶段的配置

### 需要重启服务

- 线程池大小、调度器初始化参数
- 依赖启动时一次性加载的资源配置
- SpiderData 的 Worker/Gateway retention 环境变量

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
