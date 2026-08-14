# AntCode

[![CI](https://github.com/xyuns-cc/AntCode/actions/workflows/ci.yml/badge.svg)](https://github.com/xyuns-cc/AntCode/actions/workflows/ci.yml)
[![Docker Build](https://github.com/xyuns-cc/AntCode/actions/workflows/docker-build.yml/badge.svg)](https://github.com/xyuns-cc/AntCode/actions/workflows/docker-build.yml)

**分布式任务调度与执行平台，专为规则爬虫、脚本任务、文件处理场景打造。**

控制面与执行面解耦：Master 负责调度和状态收敛，Worker 负责隔离执行，Web API 负责用户入口，Gateway 负责跨网络接入。全链路可观测（Prometheus + SSE 实时日志），支持内网直连 Redis 和公网 gRPC Gateway 两种传输模式。

---

## 快速开始

### 前置

- **PostgreSQL 14+**，一个空库
- **Redis 6+**（standalone / cluster / sentinel 都支持）
- **Python 3.11+** 和 [uv](https://github.com/astral-sh/uv)
- **Node.js 20+**（前端）

### 5 步跑通

```bash
# 1. 装依赖
uv sync --all-packages --extra dev

# 2. 复制配置模板并填关键项
cp .env.example .env
# 至少改这几个：DATABASE_URL / REDIS_URL / ENCRYPTION_KEY / ENCRYPTION_KEY_SALT / JWT_SECRET / DEFAULT_ADMIN_PASSWORD
# ENCRYPTION_KEY_SALT 每部署唯一、至少 16 字节（openssl rand -hex 16）。
# 除非 ENCRYPTION_KEY 恰好是 44 字符的 Fernet 原生 key，否则缺它会在首次加密 Worker 凭据时直接报错。

# 3. 一键初始化数据库（建表 + 建索引 + 建默认管理员）
uv run python scripts/init_db.py

# 4. 起三服务（三个独立终端）
uv run uvicorn antcode_web_api.app:app --host 0.0.0.0 --port 8000
uv run python -m antcode_master
uv run python -m antcode_worker run --name worker-1 --transport direct

# 5. 起前端
cd web/antcode-frontend && npm install && npm run dev
```

打开 <http://localhost:3000>，用 `.env` 里配的 admin 账号登录。

### 用 Makefile 更快

```bash
make install     # 装 python + node 依赖
make init-db     # 建库
make dev-api     # 起 web_api（前台）
make dev-master  # 起 master
make dev-worker  # 起 worker
make dev-web     # 起前端
```

---

## 架构一句话

- **web_api** — 用户入口 REST + SSE 实时日志流，落 PG，走 Redis 分布式限流
- **master** — 调度 + 状态收敛：13 个后台 loop，leader 抢主，其中 2 个 stream ingest loop 可分片到多实例
  - control 组（7）：scheduler / scheduler_event / scheduler_outbox / reconcile / retry / lease_sweeper / redispatch
  - ingester 组（6）：result / log_ingest / artifact_cleanup / crawl_batch_status / alert_check / worker_registration_cleanup
- **worker** — 任务执行：插件化（code / spider / render / rule），沙箱 rlimit（CPU/RAM/FSIZE），支持 Direct（Redis Streams）和 Gateway（gRPC）双传输
- **gateway** — 跨网络接入：worker 走 gRPC 到 gateway，gateway 落 Redis，master 消费

详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 核心能力

- **调度**：一次性 / 周期性 / Cron / 依赖链
- **规则爬虫**：Scrapy 2.16 引擎，5 种翻页策略（url_pattern / url_param / click_element / js_click / infinite_scroll），XPath/CSS/正则抽取，Playwright 渲染
  - **当前不支持**（安全 spool 模式不向 Rule 子进程下发 Redis / 代理凭据，env 白名单见 `services/worker/src/antcode_worker/executor/rule_policy.py`）：内容级跨 run 去重（`dedup_config` 配了也会被跳过并打 warning）、代理池（`proxy_config.enabled=true` 直接校验失败）、scrapy-redis 断点续爬（`resume_enabled=true` 直接校验失败）
  - `engine=curl_cffi`（TLS/JA3 指纹伪装）**需自行安装**：`scrapy-impersonate` 不在 pyproject 依赖里，Worker 环境未 `pip install scrapy-impersonate` 时会显式报错而非静默降级
- **可观测**：Prometheus `/metrics` 端点（HTTP QPS / 延迟 / worker 在线数 / 补派队列），SSE 实时日志，全链路 trace_id
- **可靠性**：at-least-once（XAUTOCLAIM + PEL 回收 + 跨机 SET NX 去重）、派发失败自动补派（指数退避）、优雅停机（SIGTERM + drain + deregister）
- **多租户与安全**：JWT + 项目所有权校验、登录专项限流 + 账户锁定、密钥轮换（MultiFernet）、审计日志保留策略
- **扩展性**：Redis standalone / cluster / sentinel 无感切换、master 多实例水平扩容、worker capability 路由（`WORKER_ENABLE_RULE_PLUGIN=false` 部署 code-only worker；Code/Spider/Render 插件无条件注册，**当前没有 rule-only worker 开关**）

## 目录

```
packages/           workspace 包
├── antcode_core/         # 领域模型 + 应用服务 + 基础设施（PG / Redis / cache）
├── antcode_contracts/    # gRPC proto + 生成的 pb2
└── antcode_scrapy/       # Scrapy 引擎 + sink 抽象（Redis / Gateway）

services/           独立服务
├── web_api/              # FastAPI REST + SSE 日志流
├── master/               # 调度 + 状态 loop 组
├── worker/               # 任务执行引擎
└── gateway/              # gRPC 网关（可选）

web/antcode-frontend/     # React + Antd 前端
scripts/                  # init_db.py 等
migrations/               # 首版无迁移，详见 migrations/models/README.md
infra/docker/             # Docker 部署模板
docs/                     # 文档
```

## 文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 系统架构和数据流
- [`docs/database-setup.md`](docs/database-setup.md) — 数据库初始化
- [`docs/worker-transport.md`](docs/worker-transport.md) — Direct vs Gateway 模式
- [`docs/redis-cluster.md`](docs/redis-cluster.md) — Redis 单机/集群/哨兵配置
- [`docs/master-scaling.md`](docs/master-scaling.md) — Master 多实例部署
- [`docs/worker-capabilities.md`](docs/worker-capabilities.md) — Worker 能力路由
- [`docs/mtls-deployment.md`](docs/mtls-deployment.md) — Gateway mTLS 部署
- [`docs/scheduler-api.md`](docs/scheduler-api.md) — 调度器 API 参考
- [`docs/project-api.md`](docs/project-api.md) — 项目 API 参考
- [`docs/user-api.md`](docs/user-api.md) — 用户 / 认证 API 参考
- [`docs/logs-api.md`](docs/logs-api.md) — 日志 API 参考
- [`docs/system-config.md`](docs/system-config.md) — 系统配置
- [`docs/resilience.md`](docs/resilience.md) — 熔断 / 重试 / 补派
- [`docs/scrapy-migration.md`](docs/scrapy-migration.md) — Scrapy 引擎迁移说明
- [`docs/node-env-management.md`](docs/node-env-management.md) — 多语言运行时管理

全部索引见 [`docs/README.md`](docs/README.md)。

## 部署

单机开发/测试：直接 uv 命令跑 + 前端 `npm run dev`。

Docker 部署（infra/docker/）：
```bash
cd infra/docker && cp .env.example .env
# 先启动控制面，在 Web 界面生成一次性 Worker 安装 Key
docker compose -f docker-compose.dev.yml up -d postgres redis web-api master gateway frontend
# 把安装 Key 写入 .env 的 ANTCODE_WORKER_KEY 后启动 Worker
docker compose -f docker-compose.dev.yml up -d worker
```

生产环境不使用 K8s，也不能直接复用开发 Compose。控制面必须使用精确 digest、
Docker secrets、TLS/mTLS 和 Cosign 验签，并通过唯一部署入口执行：

```bash
infra/docker/deploy-production.sh .env.production fresh-deploy
```

既有环境升级、独立 Worker、管理员 bootstrap、备份恢复和完整前置条件见
[`infra/docker/README.md`](infra/docker/README.md)。

## 版本

见 [`CHANGELOG.md`](CHANGELOG.md)。

## License

MIT
