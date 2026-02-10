# AntCode

AntCode 是一个面向生产的分布式任务执行平台，采用新架构（`packages/` + `services/`），支持任务调度、Worker 弹性扩展、实时日志与项目文件分发。

## 核心服务

| 服务 | 职责 | 默认端口 |
|---|---|---|
| `web_api` | 控制平面 API、鉴权、配置与管理 | `8000` |
| `master` | 调度循环、重试、补偿、一致性维护 | - |
| `gateway` | 公网 Worker 接入（gRPC/TLS） | `50051` |
| `worker` | 任务执行、运行时管理、日志上报 | `8001`（健康检查） |

## 运行时目录规范（最新版）

所有运行时数据统一收敛到顶层 `data/`，并严格分层：

```text
data/
├── backend/                 # 后端（web_api/master/gateway）
│   ├── db/                  # SQLite 默认位置
│   ├── logs/                # 控制平面日志
│   ├── storage/             # 后端本地存储（如启用）
│   └── keys/                # JWT / 登录加密密钥
└── worker/                  # Worker 运行时数据
    ├── projects/            # 项目缓存
    ├── runtimes/            # Python 运行时缓存
    ├── logs/                # 实时日志、wal、spool
    ├── runs/                # 运行产物
    ├── secrets/             # Worker 凭证与证书
    ├── identity/            # Worker 身份信息
    └── worker_config.yaml   # Worker 配置文件
```

> 说明：不再兼容历史运行时目录，按当前结构作为唯一标准。

## 快速开始（本地开发）

### 1) 环境准备

```bash
cp .env.example .env
uv sync
```

### 2) 启动后端服务

```bash
uv run python -m antcode_web_api
uv run python -m antcode_master
uv run python -m antcode_gateway
```

### 3) 启动 Worker

```bash
uv run python -m antcode_worker --name Worker-001 --port 8001
```

### 4) 启动前端

```bash
cd web/antcode-frontend
npm install
npm run dev
```

## Docker 开发环境

```bash
cd infra/docker
docker compose -f docker-compose.dev.yml up -d
```

详细说明见 `infra/docker/README.md`。

## 常用测试命令

```bash
uv run pytest tests/
uv run pytest tests/unit/
uv run pytest tests/unit/worker/
uv run pytest tests/unit/core/
```

## 开发约束

- 所有新功能与改动仅允许在新架构目录实现：`packages/`、`services/`。
- `src/` 为历史兼容参考目录，不作为新功能实现位置。
- 运行时数据目录为 `data/`，不得在仓库其他位置新增临时运行目录。

## 文档导航

- 文档总览：`docs/README.md`
- 系统架构：`docs/ARCHITECTURE.md`
- 数据库与迁移：`docs/database-setup.md`
- Worker 通信：`docs/worker-transport.md`
- Docker 部署：`infra/docker/README.md`
