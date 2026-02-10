# AntCode Docker 开发部署

## 目标

使用 `docker-compose.dev.yml` 一键拉起本地联调环境，覆盖：

- `web_api`（Control Plane）
- `master`（Schedule Plane）
- `gateway`（Data Plane）
- `worker`（Execution Plane）
- `mysql` / `redis` / `minio` / `frontend`

## 快速启动

```bash
cd infra/docker
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d
```

## 常用命令

```bash
# 服务状态
docker compose -f docker-compose.dev.yml ps

# 查看全部日志
docker compose -f docker-compose.dev.yml logs -f

# 查看单服务日志
docker compose -f docker-compose.dev.yml logs -f web-api

# 重建
docker compose -f docker-compose.dev.yml build --no-cache
docker compose -f docker-compose.dev.yml up -d --force-recreate
```

## 访问地址

- Frontend: `http://localhost:3000`
- Web API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Gateway gRPC: `localhost:50051`
- MinIO Console: `http://localhost:9001`

## 数据目录规范（最新版）

容器内统一使用 `/app/data`，并严格分层：

```text
/app/data/
├── backend/          # web_api / master / gateway
│   ├── db/
│   ├── logs/
│   ├── storage/
│   └── keys/
└── worker/           # worker
    ├── projects/
    ├── runtimes/
    ├── logs/
    ├── runs/
    ├── secrets/
    └── identity/
```

## Volume 说明

| Volume | 用途 |
|---|---|
| `mysql_data` | MySQL 数据 |
| `redis_data` | Redis 数据 |
| `minio_data` | MinIO 数据 |
| `worker_data` | 挂载到 `/app/data` 的运行时数据 |

## 关键环境变量

| 变量 | 说明 |
|---|---|
| `WEB_API_PORT` | Web API 端口 |
| `GATEWAY_GRPC_PORT` | Gateway gRPC 端口 |
| `FRONTEND_PORT` | 前端端口 |
| `MYSQL_*` | MySQL 账号与库配置 |
| `REDIS_PASSWORD` | Redis 密码 |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | MinIO 凭据 |
| `WORKER_MODE` / `WORKER_NAME` | Worker 基础配置 |

## 故障排查

### 容器启动失败

1. 执行 `docker compose -f docker-compose.dev.yml ps`
2. 查看异常服务日志
3. 检查 `.env` 是否缺少关键变量

### Worker 无法接单

1. 检查 Worker 与 Redis/Gateway 连通
2. 检查 Worker 传输模式配置
3. 检查 Web API 中 Worker 注册状态

### API 无法访问

1. 确认 `web-api` 健康检查通过
2. 检查端口是否被占用
3. 检查数据库与 Redis 依赖服务状态
