# AntCode

一个现代化的任务调度和项目管理平台，支持 Python 项目的自动化执行、环境管理和实时监控。

## ✨ 主要功能

- 🚀 **项目管理** - 支持 Python 脚本和代码文件的上传、管理和执行
- 📅 **任务调度** - 灵活的定时任务配置（一次性、周期性、Cron 表达式）
- 🐍 **环境管理** - 自动创建和管理虚拟环境，支持 venv 和 mise
- 📊 **实时监控** - WebSocket 实时推送任务执行状态和日志
- 📝 **日志管理** - 完整的任务执行日志记录和查询
- 💾 **多数据库支持** - SQLite/MySQL/PostgreSQL 可选
- ⚡ **缓存优化** - 支持 Redis 或内存缓存，性能优秀

## 🛠️ 技术栈

**后端：**
- FastAPI - 高性能异步 Web 框架
- Tortoise ORM - 异步 ORM
- APScheduler - 任务调度
- Redis - 缓存和任务队列（可选）

**前端：**
- React 18 + TypeScript
- Ant Design - UI 组件库
- Vite - 构建工具
- Monaco Editor - 代码编辑器

## 📦 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- uv（Python 包管理器）

### 安装步骤

1. **克隆项目**

```bash
git clone <repository-url>
cd AntCode
```

2. **配置环境变量**

```bash
cp .env.example .env
# 根据需要修改 .env 配置
```

3. **安装后端依赖**

```bash
uv sync
```

4. **安装前端依赖**

```bash
cd web/antcode-frontend
npm install
```

5. **启动后端服务**

```bash
# 回到项目根目录
cd ../..
uv run python src/main.py
```

6. **启动前端开发服务**

```bash
cd web/antcode-frontend
npm run dev
```

7. **访问应用**

- 前端地址: http://localhost:3000
- 后端 API: http://localhost:8000
- API 文档: http://localhost:8000/docs

默认管理员账号：`admin` / `admin`

## 🐳 Docker 部署

### 方式 1：使用脚本快速部署（推荐）

```bash
cd docker
chmod +x deploy.sh
./deploy.sh
```

交互式菜单包含：
- 🚀 快速启动（前端 + 后端）
- 🏗️ 分别构建前后端镜像
- 🔧 多种部署配置（SQLite/MySQL/PostgreSQL + Redis）
- 📊 查看服务状态和日志
- 🔄 重启和管理服务

### 方式 2：使用 Docker Compose

**快速启动（前后端分离）：**

```bash
cd docker
docker compose up -d
```

**分别启动前端和后端：**

```bash
# 仅启动后端
docker compose up -d antcode-backend

# 仅启动前端
docker compose up -d antcode-frontend

# 启动完整服务（含数据库和 Redis）
docker compose up -d
```

**构建镜像：**

```bash
# 构建后端镜像（SQLite）
docker build -f Dockerfile.backend -t antcode-backend:latest .

# 构建后端镜像（MySQL 支持）
docker build -f Dockerfile.backend -t antcode-backend:latest --build-arg DB_TYPE=mysql .

# 构建前端镜像
cd web/antcode-frontend
docker build -t antcode-frontend:latest .
```

### 镜像说明

- **后端镜像** (`antcode-backend`): 基于 Python 3.11-slim，包含 FastAPI 应用和所有依赖
- **前端镜像** (`antcode-frontend`): 基于 Node.js 构建 + Nginx 服务，仅包含静态文件

### 部署配置选项

| 配置 | 适用场景 | 镜像大小 |
|------|---------|---------|
| SQLite + 内存缓存 | 开发、测试 | ~200MB |
| SQLite + Redis | 小规模生产 | ~250MB |
| MySQL/PostgreSQL + Redis | 大规模生产 | ~300MB |

详细配置和故障排查请参考 [docker/README.md](docker/README.md)

## 📖 配置说明

### 环境变量

在 `.env` 文件中配置以下选项：

```env
# 数据库配置（支持 SQLite/MySQL/PostgreSQL）
DATABASE_URL=sqlite:///./antcode.sqlite3

# Redis 配置（可选，留空使用内存缓存）
REDIS_URL=redis://localhost:6379/0

# 服务器配置
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SERVER_DOMAIN=localhost

# 前端配置
FRONTEND_PORT=3000

# 日志配置
LOG_LEVEL=INFO
LOG_FORMAT=text
LOG_TO_FILE=true
LOG_FILE_PATH=./logs/app.log
```

### 数据库选择

**SQLite（默认）：** 无需额外安装
```env
DATABASE_URL=sqlite:///./antcode.sqlite3
```

**MySQL：**
```bash
pip install aiomysql cryptography
```
```env
DATABASE_URL=mysql+asyncmy://user:password@localhost:3306/antcode
```

**PostgreSQL：**
```bash
pip install asyncpg
```
```env
DATABASE_URL=postgresql://user:password@localhost:5432/antcode
```

## 📁 项目结构

```
AntCode/
├── src/                    # 后端源代码
│   ├── api/v1/            # API 路由
│   ├── core/              # 核心配置
│   ├── models/            # 数据模型
│   ├── schemas/           # Pydantic 模式
│   ├── services/          # 业务逻辑
│   └── utils/             # 工具函数
├── web/antcode-frontend/  # 前端源代码
├── storage/               # 运行时存储
├── logs/                  # 日志文件
├── migrations/            # 数据库迁移
├── docker/                # Docker 配置
└── docs/                  # 项目文档
```

## 🧪 开发指南

### 代码规范

- Python 代码遵循 PEP 8 规范
- 使用 `ruff` 进行代码检查和格式化

```bash
# 代码检查
uvx ruff check .

# 代码格式化
uvx ruff format .
```

### 运行测试

```bash
pytest -q
```

### 提交规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档更新
- `refactor:` 重构
- `test:` 测试相关
- `chore:` 其他修改

## 📚 文档

- [API 文档](docs/project-api.md)
- [调度器文档](docs/scheduler-api.md)
- [数据库配置](docs/database-setup.md)
- [Docker 部署](docker/README.md)

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

[MIT License](LICENSE)

## 👥 作者

- 项目维护者：[Your Name]

## 🔗 相关链接

- [FastAPI 文档](https://fastapi.tiangolo.com/)
- [React 文档](https://react.dev/)
- [Ant Design 文档](https://ant.design/)

