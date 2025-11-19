# AntCode Docker 部署指南

## 🚀 快速开始

### 前置要求

- Docker >= 20.10
- Docker Compose >= 2.0

### 一键部署

```bash
cd docker
./deploy.sh
```

选择 "快速启动" 即可启动前后端服务。

### 手动部署

```bash
# 启动所有服务
cd docker
docker compose up -d

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f
```

## 📦 镜像说明

项目采用前后端分离架构：

- **后端镜像** (`Dockerfile.backend`): 基于 Python 3.11-slim，约 200MB
- **前端镜像** (`web/antcode-frontend/Dockerfile`): 基于 Nginx，约 30MB

## 🏗️ 构建镜像

### 后端镜像

```bash
# SQLite 版本（默认）
docker build -f Dockerfile.backend -t antcode-backend:latest .

# MySQL 版本
docker build -f Dockerfile.backend --build-arg DB_TYPE=mysql -t antcode-backend:latest .

# PostgreSQL 版本
docker build -f Dockerfile.backend --build-arg DB_TYPE=postgres -t antcode-backend:latest .
```

### 前端镜像

```bash
cd web/antcode-frontend
docker build -t antcode-frontend:latest .
```

## 🔧 部署配置

### 方案 1: SQLite + 内存缓存（默认）

适合开发和测试：

```bash
cd docker
docker compose up -d
```

### 方案 2: MySQL/PostgreSQL + Redis

适合生产环境：

1. 编辑 `.env` 文件：

```env
# MySQL
DATABASE_URL=mysql://user:pass@mysql:3306/antcode
DB_TYPE=mysql

# 或 PostgreSQL
DATABASE_URL=postgresql://user:pass@postgres:5432/antcode
DB_TYPE=postgres

# Redis
REDIS_URL=redis://:password@redis:6379/0
```

2. 编辑 `docker-compose.yml`，取消对应服务的注释

3. 启动服务：

```bash
cd docker
docker compose up -d --build
```

## 🌐 访问地址

- 前端: http://localhost:3000
- 后端 API: http://localhost:8000
- API 文档: http://localhost:8000/docs
- 默认账号: `admin` / `admin`

## 🔍 服务管理

### 基本命令

```bash
cd docker

# 启动
docker compose up -d

# 停止
docker compose down

# 重启
docker compose restart

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f
```

### 单独管理服务

```bash
# 仅启动后端
docker compose up -d antcode-backend

# 仅启动前端
docker compose up -d antcode-frontend

# 重启后端
docker compose restart antcode-backend
```

## 🛠️ 环境变量

主要配置项（`.env` 文件）：

```env
# 服务端口
SERVER_PORT=8000
FRONTEND_PORT=3000

# 数据库
DATABASE_URL=sqlite:///./data/antcode.db
DB_TYPE=sqlite

# Redis（可选）
REDIS_URL=redis://:password@redis:6379/0

# JWT 密钥（必须修改）
JWT_SECRET_KEY=your-secret-key-here
```

详细配置请参考 [ENV_CONFIG.md](ENV_CONFIG.md)

## 🐛 故障排查

### 查看日志

```bash
cd docker

# 所有服务
docker compose logs -f

# 指定服务
docker compose logs -f antcode-backend
docker compose logs -f antcode-frontend
```

### 进入容器

```bash
# 后端容器
docker exec -it antcode-backend /bin/bash

# 前端容器
docker exec -it antcode-frontend /bin/sh
```

### 重新构建

```bash
cd docker
docker compose build --no-cache
docker compose up -d --force-recreate
```

### 常见问题

**Q: 后端启动失败**
- 检查 `.env` 配置
- 确认数据库连接正确
- 查看日志：`docker compose logs antcode-backend`

**Q: 前端无法访问后端**
- 确认后端服务已启动
- 检查网络连接：`docker network inspect docker_antcode-network`
- 查看健康检查：`docker compose ps`

**Q: 数据丢失**
- 确认 `volumes` 配置正确
- 使用命名卷而非绑定挂载
- 定期备份数据

## 🔐 生产环境建议

1. **修改默认密码**
   - JWT_SECRET_KEY
   - 数据库密码
   - Redis 密码

2. **限制端口访问**
   ```yaml
   ports:
     - "127.0.0.1:8000:8000"
   ```

3. **配置资源限制**
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '2'
         memory: 2G
   ```

4. **数据备份**
   ```bash
   # 备份存储
   docker cp antcode-backend:/app/storage ./backup/
   
   # 备份数据库
   docker exec antcode-backend cp /app/data/antcode.db /app/data/backup.db
   ```

5. **日志轮转**
   ```yaml
   logging:
     driver: "json-file"
     options:
       max-size: "10m"
       max-file: "3"
   ```

## 📚 相关文档

- [ENV_CONFIG.md](ENV_CONFIG.md) - 环境变量详细说明
- [../README.md](../README.md) - 项目主文档
- [../docs/](../docs/) - API 和系统文档

## 💬 获取帮助

遇到问题？
1. 查看本文档的故障排查部分
2. 检查日志：`docker compose logs -f`
3. 提交 GitHub Issue
