# AntCode

一个现代化的任务调度和项目管理平台，支持 Python 项目的自动化执行、环境管理和实时监控。

## ✨ 主要功能

- 🚀 **项目管理** - 支持 Python 脚本和代码文件的上传、管理和执行
- 📅 **任务调度** - 灵活的定时任务配置（一次性、周期性、Cron 表达式）
- 🐍 **环境管理** - 自动创建和管理虚拟环境，支持 venv 和 mise
- 📊 **实时监控** - WebSocket 实时推送任务执行状态和日志
- 📝 **日志管理** - 完整的任务执行日志记录和查询
- 💾 **多数据库支持** - SQLite/MySQL/PostgreSQL 可选
- ⚡ **缓存优化** - 支持 Redis 或内存缓存

## 🛠️ 技术栈

**后端：**
- FastAPI - 高性能异步 Web 框架
- Tortoise ORM - 异步 ORM
- APScheduler - 任务调度
- Redis - 缓存和任务队列（可选）
- Scrapy / DrissionPage - 爬虫框架

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
git clone https://github.com/xyuns-cc/AntCode.git
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
uv run python -m src.main
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

```bash
cd docker
docker compose up -d
```

详细配置请参考 [docker/README.md](docker/README.md)

## 📖 环境变量配置

在 `.env` 文件中配置：

```env
# 数据库配置（留空使用默认 SQLite）
DATABASE_URL=

# Redis 配置（留空使用内存缓存）
REDIS_URL=

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
```

### 数据库配置示例

```env
# SQLite（默认，无需配置）
DATABASE_URL=

# MySQL
DATABASE_URL=mysql+asyncmy://user:password@localhost:3306/antcode

# PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/antcode
```

## 📁 项目结构

```
AntCode/
├── src/                        # 后端源代码
│   ├── api/v1/                 # API 路由
│   ├── core/                   # 核心模块（配置、认证、缓存、日志等）
│   ├── models/                 # 数据模型
│   ├── schemas/                # Pydantic 模式
│   ├── services/               # 业务逻辑
│   │   ├── envs/               # 环境管理服务
│   │   ├── files/              # 文件服务
│   │   ├── logs/               # 日志服务
│   │   ├── monitoring/         # 监控服务
│   │   ├── projects/           # 项目服务
│   │   ├── scheduler/          # 调度服务
│   │   ├── users/              # 用户服务
│   │   └── websockets/         # WebSocket 服务
│   ├── tasks/                  # 爬虫任务
│   ├── utils/                  # 工具函数
│   └── main.py                 # 应用入口
├── web/antcode-frontend/       # 前端源代码
│   ├── src/
│   │   ├── components/         # 组件
│   │   ├── pages/              # 页面
│   │   ├── services/           # API 服务
│   │   ├── stores/             # 状态管理
│   │   └── hooks/              # 自定义 Hooks
│   └── ...
├── docker/                     # Docker 配置
├── docs/                       # 项目文档
├── data/                       # 运行时数据（自动生成，不提交）
│   ├── db/                     # 数据库文件
│   ├── logs/                   # 日志文件
│   └── storage/                # 项目存储
├── pyproject.toml              # Python 项目配置
└── uv.lock                     # 依赖锁定文件
```

## 📄 许可证

[MIT License](LICENSE)
