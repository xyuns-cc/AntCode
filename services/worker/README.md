# AntCode Worker（Execution Plane）

Worker 负责执行任务、管理运行时环境、上报日志与心跳。

## 目录结构（核心）

```text
services/worker/
├── src/antcode_worker/
│   ├── app/                # 依赖装配
│   ├── engine/             # 任务执行编排
│   ├── executor/           # 进程执行与隔离
│   ├── heartbeat/          # 心跳上报
│   ├── logs/               # 实时日志 / WAL / Spool
│   ├── plugins/            # code/spider/render 插件
│   ├── projects/           # 项目缓存与拉取
│   ├── runtime/            # uv 运行时管理
│   ├── security/           # 凭证与安全工具
│   └── transport/          # direct / gateway 传输层
└── config/                 # 示例配置
```

## 启动方式

```bash
# 交互式
uv run python -m antcode_worker

# 查看帮助
uv run python -m antcode_worker --help

# 诊断
uv run python -m antcode_worker doctor

# 启动节点
uv run python -m antcode_worker --name Worker-001 --port 8001
```

## 传输模式

- `direct`：内网直连 Redis
- `gateway`：通过 gRPC Gateway 公网接入

推荐显式设置：

```env
WORKER_TRANSPORT_MODE=gateway
```

## 运行时数据目录

默认目录：`data/worker`

```text
data/worker/
├── worker_config.yaml
├── projects/
├── runtimes/
├── logs/
├── runs/
├── secrets/
└── identity/
```

## 关键配置项

| 变量 | 说明 |
|---|---|
| `WORKER_DATA_DIR` | Worker 数据根目录 |
| `WORKER_TRANSPORT_MODE` | `direct` / `gateway` |
| `WORKER_REDIS_URL` | Direct 模式 Redis 地址 |
| `WORKER_GATEWAY_ENDPOINT` | Gateway 模式地址（`host:port`） |
| `ANTCODE_WORKER_KEY` | 安装 Key（节点注册） |

## 约束

- Worker 仅写入 `data/worker`
- 不应在仓库其他目录创建运行时文件
