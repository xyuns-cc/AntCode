# 👷 AntCode Worker (执行器)

Worker 是 AntCode 的"手脚"，负责实际执行用户提交的任务。它被设计为无状态、即插即用的组件，支持动态扩缩容。

---

## 🌟 核心职责

1.  **任务执行 (Execution)**: 从队列拉取任务，启动隔离的 Python 运行时环境执行代码。
2.  **环境管理 (Runtime Management)**: 自动安装 `uv`，并为每个项目创建独立的虚拟环境，确保依赖不冲突。
3.  **实时日志 (Real-time Logging)**: 捕获任务的 `stdout/stderr`，并通过流式传输实时上报给后端。
4.  **心跳保活 (Heartbeat)**: 定期上报 Worker 状态（CPU/内存/任务数），供控制面决策调度。

---

## 🚀 启动指南

### 方式一：交互式启动 (推荐新手)

```bash
uv run python -m antcode_worker
```
系统会引导你输入 Worker 名称、选择接入模式等配置。

### 方式二：命令行参数启动 (推荐脚本/容器)

```bash
uv run python -m antcode_worker --name "My-Worker-01" --port 8001
```

### 方式三：环境变量配置 (推荐生产环境)

你可以通过环境变量预设配置。生产环境仅支持 Gateway；Direct 只用于可信内网、
单租户测试。生产 Gateway Worker 必须设置 `WORKER_GATEWAY_BACKENDLESS=true`，
并保证 `DATABASE_URL`、`REDIS_URL`、`WORKER_REDIS_URL` 均为空。

| 变量名 | 描述 | 示例 |
| :--- | :--- | :--- |
| `WORKER_NAME` | Worker 名称 | `prod-worker-01` |
| `WORKER_TRANSPORT_MODE` | 接入模式 | `gateway` (默认 `gateway`) |
| `WORKER_GATEWAY_ENDPOINT` | Gateway 地址 | `gateway.example.com:50051` |
| `WORKER_REDIS_URL` | Redis 地址 (Direct 模式) | `redis://192.168.1.10:6379/0` |
| `WORKER_CREDENTIAL_STORE` | 凭证存储，裸机默认 `persistent` | `persistent` |

---

## 📂 运行时数据结构

Worker 所有的运行时产生的数据都存储在 `data/worker` 目录下。`WORKER_DATA_DIR` 只能指向项目根 `data/` 下的目录：

```text
data/worker/
├── runtimes/      # Python 虚拟环境 (按项目+环境 hash 隔离)
├── runs/          # 任务执行时的临时工作目录
├── temp/          # 运行时构建与沙箱临时文件
├── secrets/       # 原子持久化 Worker 凭证（目录 0700，文件 0600）
└── identity/      # Worker 身份标识 (UUID)
```

---

## 🛠️ 常见操作

### 运行环境诊断
如果遇到依赖安装失败或网络问题，请运行诊断工具：
```bash
uv run python -m antcode_worker doctor
```

### 查看帮助
获取完整的参数列表：
```bash
uv run python -m antcode_worker --help
```
