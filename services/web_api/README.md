# 🌐 AntCode Web API (控制面)

Web API 是 AntCode 的"大脑"，提供 RESTful API 接口，负责处理外部的所有交互请求，包括用户操作、前端查询以及 Worker 的状态上报。

---

## 🎯 核心职责

1.  **用户接口 (User Interface)**: 提供任务管理、项目配置、日志查询等 API。
2.  **Worker 管理 (Worker Mgmt)**: 处理 Worker 的注册、鉴权与心跳接收。
3.  **配置中心 (Config Center)**: 管理系统全局配置与项目级配置。
4.  **元数据存储 (Metadata Store)**: 负责所有持久化数据的 CRUD 操作。

---

## ⚡ 快速启动

### 命令行启动

```bash
uv run python -m antcode_web_api
```

### 推荐配置

| 变量名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `BIND_HOST` | `0.0.0.0` | 监听地址 |
| `SERVER_PORT` | `8000` | 监听端口 |
| `DATABASE_URL` | - | 数据库连接串 |
| `ENCRYPTION_KEY` | - | 加密密钥（用于凭证加密等） |

---

## 📚 接口文档

启动服务后，访问以下地址查看自动生成的交互式文档：

-   **Swagger UI**: `http://localhost:8000/docs`
-   **ReDoc**: `http://localhost:8000/redoc`

---

## 🏗️ 模块结构

-   `routes/`: 路由定义 (Routers)
-   `services/`: 业务逻辑层 (Service Layer)
-   `middleware/`: 中间件（限流、鉴权、trace 等）
-   `streams/`: SSE 实时日志流
-   `app_factory.py` / `lifespan.py`: 应用装配与生命周期

数据模型（Tortoise ORM）与 Pydantic Schema 不在本服务内，统一放 `antcode_core.domain`。
