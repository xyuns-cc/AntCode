# Docker 快速部署指南

## 🚀 快速开始

### 1. 准备配置文件

```bash
# 复制配置示例
cp .env.example .env

# 编辑配置（可选）
vim .env
```

### 2. 启动服务

```bash
cd docker
docker-compose up -d
```

### 3. 访问应用

- **Web 控制台**: http://localhost:3000
- **API 地址**: http://localhost:8000
- **API 文档**: http://localhost:8000/docs
- **默认账号**: admin / admin

---

## 📦 使用镜像 vs 本地构建

### 使用预构建镜像（推荐）

```yaml
# docker-compose.yml（默认配置）
services:
  antcode-api:
    image: ${BACKEND_IMAGE:-ghcr.io/your-org/antcode-api:latest}
  antcode-web:
    image: ${FRONTEND_IMAGE:-ghcr.io/your-org/antcode-frontend:latest}
```

> 可通过设置环境变量 `BACKEND_IMAGE`、`FRONTEND_IMAGE` 来切换到你自己的仓库镜像（例如 GitHub Container Registry）。

直接启动：
```bash
docker-compose up -d
```

### 本地构建镜像

```yaml
# docker-compose.yml
antcode-api:
  # image: ghcr.io/your-org/antcode-api:latest  # 注释这行
  build:
    context: ..
    dockerfile: docker/Dockerfile
    target: backend-runtime
    args:
      DB_TYPE: ${DB_TYPE:-sqlite}

antcode-web:
  # image: ghcr.io/your-org/antcode-frontend:latest  # 注释这行
  build:
    context: ..
    dockerfile: docker/Dockerfile
    target: frontend-runtime
    args:
      VITE_API_BASE_URL: ${VITE_API_BASE_URL:-http://antcode-api:8000}
      VITE_APP_TITLE: ${VITE_APP_TITLE:-AntCode Task Platform}
```

构建并启动：
```bash
docker-compose up -d --build
```

---

### 前端运行时配置（环境变量注入）

`antcode-web` 在容器启动时会读取以下环境变量动态生成 `env-config.js`，不再依赖在构建阶段写死配置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `API_BASE_URL` | 后端 API 根地址 | `http://antcode-api:8000` |
| `WS_BASE_URL` | WebSocket 根地址 | `ws://antcode-api:8000` |
| `APP_TITLE` | 页面标题/品牌文案 | `AntCode Task Platform` |
| `APP_VERSION` | 显示用版本号 | `1.0.0` |

在 `docker-compose.yml` 中，这些变量会自动引用仓库根目录 `.env` 里的 `VITE_*` 配置，也可以通过 `docker compose` 命令的 `-e` 或 CI/CD Secrets 单独覆盖。

---

### GitHub Actions 自动构建镜像

仓库内新增 `.github/workflows/docker-images.yml`，在 push、打 tag 或手动触发时会：

1. 并行构建前端（`frontend-runtime`）与后端（`backend-runtime`）镜像
2. 推送到 `ghcr.io/<your-org>/antcode-{api|frontend}`，标签包含 `latest`、git tag、commit SHA

使用方式：

1. 在 GitHub 仓库启用 GitHub Packages（默认已可用）
2. 可选：在仓库 **Settings → Variables** 中新增
   - `VITE_API_BASE_URL`：前端构建时注入的 API 基地址
   - `VITE_APP_TITLE`：前端界面标题
   - `DOCKER_DB_TYPE`：后端构建所需数据库依赖（`sqlite`/`mysql`/`postgres`/`all`）
3. 推送代码或手动运行 workflow
4. 登录后即可拉取镜像：

```bash
echo "${GITHUB_TOKEN}" | docker login ghcr.io -u <github-username> --password-stdin
docker pull ghcr.io/<your-org>/antcode-api:latest
docker pull ghcr.io/<your-org>/antcode-frontend:latest
```

---

## 🗄️ 数据库配置

### SQLite（默认，无需额外配置）

**.env 配置**：
```bash
DATABASE_URL=sqlite:///./antcode.sqlite3
```

**docker-compose.yml**：无需修改，默认即可。

---

### 启用 MySQL

**1. 修改 .env**：
```bash
DB_TYPE=mysql
DATABASE_URL=mysql+asyncmy://antcode:antcode_password@mysql:3306/antcode
MYSQL_ROOT_PASSWORD=root_password
MYSQL_DATABASE=antcode
MYSQL_USER=antcode
MYSQL_PASSWORD=antcode_password
```

**2. 取消注释 docker-compose.yml**：
- 取消注释 `mysql` 服务（第 41-61 行）
- 取消注释 `depends_on` 中的 `mysql`（第 27-29 行）

**3. 启动**：
```bash
docker-compose up -d --build
```

---

### 启用 PostgreSQL

**1. 修改 .env**：
```bash
DB_TYPE=postgres
DATABASE_URL=postgresql://antcode:antcode_password@postgres:5432/antcode
POSTGRES_USER=antcode
POSTGRES_PASSWORD=antcode_password
POSTGRES_DB=antcode
```

**2. 取消注释 docker-compose.yml**：
- 取消注释 `postgres` 服务（第 63-80 行）
- 取消注释 `depends_on` 中的 `postgres`（第 30-32 行）

**3. 启动**：
```bash
docker-compose up -d --build
```

---

### 启用 Redis

**1. 修改 .env**：
```bash
REDIS_URL=redis://:redis_password@redis:6379/0
REDIS_PASSWORD=redis_password
```

**2. 取消注释 docker-compose.yml**：
- 取消注释 `redis` 服务（第 82-95 行）
- 取消注释 `depends_on` 中的 `redis`（第 33-35 行）

**3. 启动**：
```bash
docker-compose up -d
```

---

## 🔧 常用命令

### 服务管理

```bash
# 启动所有服务
docker-compose up -d

# 停止所有服务
docker-compose down

# 重启服务
docker-compose restart

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs -f antcode-api
```

### 数据管理

```bash
# 备份数据
docker-compose exec antcode-api tar -czf /tmp/backup.tar.gz /app/storage /app/data
docker cp antcode-api:/tmp/backup.tar.gz ./backup-$(date +%Y%m%d).tar.gz

# 清理所有数据（⚠️ 危险操作）
docker-compose down -v
rm -rf ./data/*
```

### 进入容器

```bash
# 进入 API 容器
docker-compose exec antcode-api bash

# 进入 MySQL 容器
docker-compose exec mysql mysql -uantcode -pantcode_password antcode

# 进入 PostgreSQL 容器
docker-compose exec postgres psql -U antcode -d antcode

# 进入 Redis 容器
docker-compose exec redis redis-cli -a redis_password
```

---

## 📊 健康检查

所有服务都配置了健康检查，查看状态：

```bash
docker-compose ps

# 输出示例：
# NAME            STATUS                   PORTS
# antcode-api     Up (healthy)             127.0.0.1:8000->8000/tcp
# antcode-mysql   Up (healthy)             3306/tcp
# antcode-redis   Up (healthy)             6379/tcp
```

---

## 🐛 故障排查

### 问题 1: 服务启动失败

```bash
# 查看详细日志
docker-compose logs -f antcode-api

# 检查配置
docker-compose config
```

### 问题 2: 数据库连接失败

```bash
# 检查数据库服务是否健康
docker-compose ps mysql

# 测试数据库连接
docker-compose exec mysql mysqladmin ping -h localhost
```

### 问题 3: 端口被占用

**修改 .env**：
```bash
SERVER_PORT=8001  # 改为其他端口
```

### 问题 4: 权限问题

```bash
# 修复目录权限
chmod -R 755 ./data ./storage ./logs
```

---

## 🔐 生产环境安全配置

### 1. 修改默认密码

**.env**：
```bash
JWT_SECRET_KEY=$(openssl rand -hex 32)
MYSQL_ROOT_PASSWORD=$(openssl rand -base64 24)
MYSQL_PASSWORD=$(openssl rand -base64 24)
REDIS_PASSWORD=$(openssl rand -base64 24)
```

### 2. 限制端口暴露

**docker-compose.yml**：
```yaml
ports:
  - "127.0.0.1:8000:8000"  # 只监听本地，不暴露到外网
```

### 3. 使用 HTTPS

配置反向代理（Nginx/Caddy），在代理层处理 HTTPS。

---

## 📝 配置文件说明

### 目录结构

```
docker/
├── Dockerfile              # 镜像构建文件
├── docker-compose.yml      # 服务编排配置
├── data/                   # 数据持久化目录
│   ├── mysql/             # MySQL 数据
│   ├── postgres/          # PostgreSQL 数据
│   └── redis/             # Redis 数据
└── README.md              # 本文档
```

### 数据持久化

所有重要数据都挂载到本地：

- `./data/` - 数据库和 SQLite 文件
- `../storage/` - 项目文件存储
- `../logs/` - 应用日志

---

## 🎯 推荐配置

| 场景 | 配置 | 说明 |
|------|------|------|
| **快速测试** | SQLite | 最简单，开箱即用 |
| **开发环境** | SQLite + Redis | 性能更好 |
| **生产环境** | MySQL + Redis | 最佳性能和稳定性 |

---

## ✅ 完整示例

### MySQL + Redis 生产配置

**1. .env**：
```bash
# 数据库
DB_TYPE=mysql
DATABASE_URL=mysql+asyncmy://antcode:SecurePass123@mysql:3306/antcode
MYSQL_ROOT_PASSWORD=RootPass456
MYSQL_PASSWORD=SecurePass123

# Redis
REDIS_URL=redis://:RedisPass789@redis:6379/0
REDIS_PASSWORD=RedisPass789

# JWT
JWT_SECRET_KEY=your-very-long-random-secret-key-here

# 服务器
SERVER_PORT=8000
LOG_LEVEL=INFO
```

**2. docker-compose.yml**：
取消注释 `mysql`、`redis` 服务和 `depends_on`。

**3. 启动**：
```bash
cd docker
docker-compose up -d --build
```

**4. 验证**：
```bash
# 检查服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 测试 API
curl http://localhost:8000/api/v1/health
```

---

## 🎉 总结

- ✅ 使用 `.env` 统一管理配置
- ✅ 默认配置即可快速启动
- ✅ 需要时取消注释即可启用服务
- ✅ 完整的健康检查和自动重启
- ✅ 数据持久化到本地目录

**快速开始**：
```bash
cp .env.example .env
cd docker
docker-compose up -d
```

然后访问 http://localhost:8000 🚀
