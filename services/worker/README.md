# 👷 AntCode Worker (执行器)

Worker 是 AntCode 的"手脚"，负责实际执行用户提交的任务。它被设计为无状态、即插即用的组件，支持动态扩缩容。

---

## 🌟 核心职责

1.  **任务执行 (Execution)**: 从队列拉取任务，启动隔离的 Python 运行时环境执行代码。
2.  **环境管理 (Runtime Management)**: 自动安装 `uv`，并为每个项目创建独立的虚拟环境，确保依赖不冲突。
3.  **实时日志 (Real-time Logging)**: 捕获任务的 `stdout/stderr`，并通过流式传输实时上报给后端。
4.  **心跳保活 (Heartbeat)**: 定期上报 Worker 状态（CPU/内存/任务数），供控制面决策调度。

---

## 🧭 两种部署方式

Worker 官方支持 **Docker** 与 **物理机（宿主进程）**，两种方式都能跑 Direct 与 Gateway。
完整矩阵、宿主前提与安全姿态差异见 [`docs/worker-transport.md`](../../docs/worker-transport.md)。
一句话结论：**Docker 为了让 bwrap 能建沙箱，必须先放宽 seccomp / AppArmor / systempaths
三项容器级限制；物理机不需要任何放宽，隔离严格更强**，所以不可信 / 多租户生产任务默认
走物理机或独立 VM，Docker 路径用于可信内网与验收环境。

### 物理机部署前提（fail-closed）

```bash
sudo apt install bubblewrap util-linux git         # bwrap 与 prlimit 是硬依赖
sysctl kernel.unprivileged_userns_clone            # 必须为 1
ulimit -Hn                                         # 必须 >= 2048（执行器对每个子进程 setrlimit）
uv --version                                       # 运行 Worker 与创建任务运行时
mise --version                                     # 可选；不装则多语言任务不可用，Python 任务不受影响
```

私有 / 企业 CA 的控制面还必须设 `SSL_CERT_FILE` 指向 CA bundle——httpx 不读操作系统
信任库，`update-ca-certificates` 不生效。

**观测端口默认绑 `0.0.0.0`**。容器路径不发布该端口，物理机没有这层遮挡：`/metrics` 与
`/health/ready` 会无鉴权暴露在宿主每个网卡上。物理机部署请设 `WORKER_HOST=127.0.0.1`
（或用防火墙收口）——绑回环不影响注册上报的地址，控制台仍显示路由出口 IP。

**部署前确认项目根 `.env` 里没有 `WORKER_ID`**：物理机 Worker 就跑在源码树里，残留的
`WORKER_ID` 会覆盖注册得到的身份，Gateway 侧只会报 `mTLS 证书身份与 worker_id 不匹配`。
同理，Direct 部署前确认 `.env` 里**没有 `REDIS_URL`**：Direct Worker 检出它会 fail-closed
拒启，而报错不会指向 `.env`。

跑 Direct 时 `WORKER_REDIS_URL` 只填 `redis://<host>:<port>/<db>`，**不要带任何凭据**：
逐 Worker 的最小权限 ACL 账号由控制面在注册后签发、运行期注入，手填的口令只会被覆盖。
Direct 还必须给 `DATABASE_URL`——它的产物平面直连 PG blob 存储。

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
