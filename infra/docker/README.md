# AntCode Docker 部署指南

## 架构概述

AntCode 采用多服务架构，分为四个平面：

| 服务 | 平面 | 职责 |
|------|------|------|
| web-api | Control Plane | HTTP API、WebSocket/SSE、鉴权、审计 |
| master | Schedule Plane | 调度循环、TaskRun 生成、重试、补偿 |
| gateway | Data Plane | 公网 Worker 接入、gRPC/TLS、认证、限流 |
| worker | Execution Plane | 任务执行、uv runtime、日志、心跳 |

## 快速开始

### 前置要求

- Docker >= 20.10
- Docker Compose >= 2.0

### 开发环境启动

```bash
cd infra/docker

# 启动所有服务
docker compose -f docker-compose.dev.yml up -d

# 查看状态
docker compose -f docker-compose.dev.yml ps

# 查看日志
docker compose -f docker-compose.dev.yml logs -f
```

### 访问地址

- 前端: http://localhost:3000
- Web API: http://localhost:8000
- API 文档: http://localhost:8000/docs
- MinIO 控制台: http://localhost:9001
- 默认账号: `admin` / `admin`

## 服务说明

### Web API (Control Plane)

提供 HTTP REST API 和 WebSocket/SSE 实时推送。

```bash
# 单独启动
docker compose -f docker-compose.dev.yml up -d web-api

# 查看日志
docker compose -f docker-compose.dev.yml logs -f web-api
```

端口: 8000

### Master (Schedule Plane)

运行调度循环，负责任务投递、重试、补偿。

```bash
# 单独启动
docker compose -f docker-compose.dev.yml up -d master

# 查看日志
docker compose -f docker-compose.dev.yml logs -f master
```

特点:
- 支持多实例部署，通过 Redis 分布式锁选主
- 使用 fencing token 防止脑裂

### Gateway (Data Plane)

公网 Worker 接入网关，提供 gRPC/TLS 服务。

```bash
# 单独启动
docker compose -f docker-compose.dev.yml up -d gateway

# 查看日志
docker compose -f docker-compose.dev.yml logs -f gateway
```

端口: 50051 (gRPC)

特点:
- 支持 mTLS/API Key 认证
- 内置限流保护
- 代理 Worker 拉取任务

### Worker (Execution Plane)

任务执行器，支持两种模式：

1. **Direct 模式**: 内网直连 Redis Streams
2. **Gateway 模式**: 公网通过 Gateway gRPC 接入

```bash
# Direct 模式启动
docker compose -f docker-compose.dev.yml up -d worker

# 查看日志
docker compose -f docker-compose.dev.yml logs -f worker
```

特点:
- 使用 uv 管理 Python 运行时
- 支持任务超时和强杀
- 实时日志流 + 归档上传

## 环境变量

### 数据库配置

```env
MYSQL_ROOT_PASSWORD=root_password
MYSQL_DATABASE=antcode
MYSQL_USER=antcode
MYSQL_PASSWORD=antcode_password
```

### Redis 配置

```env
REDIS_PASSWORD=redis_password
```

### MinIO 配置

```env
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
```

### 服务端口

```env
WEB_API_PORT=8000
GATEWAY_GRPC_PORT=50051
FRONTEND_PORT=3000
```

### Worker 配置

```env
WORKER_MODE=direct  # direct 或 gateway
WORKER_NAME=worker-1
```

## 构建镜像

### 构建所有服务

```bash
cd infra/docker
docker compose -f docker-compose.dev.yml build
```

### 单独构建

```bash
# Web API
docker build -f infra/docker/Dockerfile.web_api -t antcode-web-api:dev .

# Master
docker build -f infra/docker/Dockerfile.master -t antcode-master:dev .

# Gateway
docker build -f infra/docker/Dockerfile.gateway -t antcode-gateway:dev .

# Worker
docker build -f infra/docker/Dockerfile.worker -t antcode-worker:dev .
```

## 数据持久化

使用 Docker volumes 持久化数据：

| Volume | 用途 |
|--------|------|
| mysql_data | MySQL 数据 |
| redis_data | Redis 数据 |
| minio_data | MinIO 对象存储 |
| worker_data | Worker 运行数据 |
| worker_venvs | Worker Python 虚拟环境 |

## 网络架构

所有服务通过 `antcode-network` 桥接网络通信：

```
┌──────────┐     HTTP/WS      ┌──────────┐
│ Frontend │◄────────────────►│ Web API  │
└──────────┘                   └────┬─────┘
                                    │
                               ┌────▼─────┐
                               │  Redis   │
                               └────┬─────┘
                                    │
        gRPC/TLS                    │
┌──────────┐  ─────────────────► ┌──▼───────┐      ┌──────────┐
│  Worker  │                     │ Gateway  │      │  Master  │
└──────────┘  ◄───────────────── └──────────┘      └────┬─────┘
       ▲                                                 │
       │                                                 ▼
       └──────────── Redis Streams ──────────────► ┌──────────┐
                                                   │  MySQL   │
                                                   └──────────┘
```

## 故障排查

### 查看服务状态

```bash
docker compose -f docker-compose.dev.yml ps
```

### 查看日志

```bash
# 所有服务
docker compose -f docker-compose.dev.yml logs -f

# 指定服务
docker compose -f docker-compose.dev.yml logs -f web-api
```

### 进入容器

```bash
docker exec -it antcode-web-api /bin/bash
docker exec -it antcode-master /bin/bash
docker exec -it antcode-gateway /bin/bash
docker exec -it antcode-worker /bin/bash
```

### 重新构建

```bash
docker compose -f docker-compose.dev.yml build --no-cache
docker compose -f docker-compose.dev.yml up -d --force-recreate
```

## 生产环境建议

1. **安全配置**
   - 修改所有默认密码
   - 启用 TLS/mTLS
   - 限制端口访问

2. **资源限制**
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '2'
         memory: 2G
   ```

3. **日志轮转**
   ```yaml
   logging:
     driver: "json-file"
     options:
       max-size: "10m"
       max-file: "3"
   ```

4. **数据备份**
   ```bash
   # 备份 MySQL
   docker exec antcode-mysql mysqldump -u root -p antcode > backup.sql
   
   # 备份 Redis
   docker exec antcode-redis redis-cli -a password BGSAVE
   ```

## 相关文档

- [项目主文档](../../README.md)
- [架构设计](../../docs/ARCHITECTURE.md)
- [API 文档](../../docs/project-api.md)
