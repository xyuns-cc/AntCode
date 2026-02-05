# AntCode 后端服务

基于 FastAPI + Tortoise ORM 的高性能异步后端服务，提供 REST API 和 gRPC 服务。

## 📁 目录结构

```
src/
├── api/                        # API 路由层
│   └── v1/                     # v1 版本 API
│       ├── base.py             # 基础路由（健康检查）
│       ├── users.py            # 用户管理
│       ├── project.py          # 项目管理
│       ├── scheduler.py        # 任务调度
│       ├── logs.py             # 日志查询
│       ├── nodes.py            # 节点管理
│       ├── envs.py             # 环境管理
│       ├── monitoring.py       # 监控指标
│       ├── alert.py            # 告警配置
│       ├── audit.py            # 审计日志
│       ├── dashboard.py        # 仪表盘数据
│       ├── system_config.py    # 系统配置
│       ├── grpc_metrics.py     # gRPC 性能指标
│       └── websocket_logs.py   # WebSocket 日志推送
│
├── core/                       # 核心模块
│   ├── config.py               # 配置管理（Pydantic Settings）
│   ├── logging.py              # 日志配置（Loguru）
│   ├── exceptions.py           # 自定义异常
│   ├── response.py             # 统一响应格式
│   ├── command_runner.py       # 命令执行器
│   └── security/               # 安全模块
│       ├── auth.py             # JWT 认证
│       └── permissions.py      # 权限控制
│
├── models/                     # 数据库模型（Tortoise ORM）
│   ├── base.py                 # 基础模型
│   ├── user.py                 # 用户模型
│   ├── project.py              # 项目模型
│   ├── scheduler.py            # 调度任务模型
│   ├── node.py                 # 节点模型
│   ├── node_project.py         # 节点-项目关联
│   ├── envs.py                 # 环境模型
│   ├── monitoring.py           # 监控数据模型
│   ├── audit_log.py            # 审计日志模型
│   ├── system_config.py        # 系统配置模型
│   └── enums.py                # 枚举定义
│
├── schemas/                    # Pydantic 模式
│   ├── base.py                 # 基础响应模式
│   ├── common.py               # 通用模式
│   ├── user.py                 # 用户请求/响应
│   ├── project.py              # 项目请求/响应
│   ├── project_unified.py      # 统一项目模式
│   ├── scheduler.py            # 调度请求/响应
│   ├── node.py                 # 节点请求/响应
│   ├── envs.py                 # 环境请求/响应
│   ├── logs.py                 # 日志请求/响应
│   ├── monitoring.py           # 监控请求/响应
│   ├── alert.py                # 告警请求/响应
│   └── system_config.py        # 系统配置请求/响应
│
├── services/                   # 业务逻辑层
│   ├── base.py                 # 服务基类
│   │
│   ├── users/                  # 用户服务
│   │   └── user_service.py
│   │
│   ├── projects/               # 项目服务
│   │   ├── project_service.py          # 项目 CRUD
│   │   ├── project_file_service.py     # 文件管理
│   │   ├── project_sync_service.py     # 项目同步
│   │   ├── unified_project_service.py  # 统一项目服务
│   │   ├── relation_service.py         # 关联管理
│   │   └── temp_cleanup_service.py     # 临时文件清理
│   │
│   ├── scheduler/              # 调度服务
│   │   ├── scheduler_service.py    # 调度管理
│   │   ├── task_executor.py        # 任务执行
│   │   ├── spider_dispatcher.py    # 爬虫分发
│   │   ├── retry_service.py        # 重试服务
│   │   ├── queue_backend.py        # 队列后端抽象
│   │   ├── memory_queue.py         # 内存队列
│   │   ├── redis_queue.py          # Redis 队列
│   │   ├── execution_resolver.py   # 执行解析
│   │   └── task_persistence.py     # 任务持久化
│   │
│   ├── nodes/                  # 节点服务
│   │   ├── node_service.py             # 节点管理
│   │   ├── node_dispatcher.py          # 任务分发
│   │   ├── node_project_service.py     # 节点项目管理
│   │   ├── distributed_log_service.py  # 分布式日志
│   │   └── resource_limits_service.py  # 资源限制
│   │
│   ├── grpc/                   # gRPC 服务
│   │   ├── server.py               # gRPC 服务器
│   │   ├── node_service_impl.py    # 节点服务实现
│   │   ├── dispatcher.py           # 消息分发
│   │   ├── config.py               # gRPC 配置
│   │   ├── metrics.py              # 性能指标
│   │   ├── performance.py          # 性能监控
│   │   └── handlers/               # 消息处理器
│   │       ├── heartbeat_handler.py    # 心跳处理
│   │       ├── log_handler.py          # 日志处理
│   │       ├── task_status_handler.py  # 任务状态处理
│   │       └── task_dispatcher.py      # 任务分发处理
│   │
│   ├── logs/                   # 日志服务
│   │   ├── task_log_service.py         # 任务日志
│   │   ├── log_cleanup_service.py      # 日志清理
│   │   ├── log_performance_service.py  # 日志性能
│   │   └── log_security_service.py     # 日志安全
│   │
│   ├── envs/                   # 环境服务
│   │   ├── python_env_service.py   # Python 环境
│   │   └── venv_service.py         # venv 管理
│   │
│   ├── files/                  # 文件服务
│   │   ├── file_storage.py             # 文件存储
│   │   └── async_file_stream_service.py # 异步文件流
│   │
│   ├── websockets/             # WebSocket 服务
│   │   ├── websocket_connection_manager.py  # 连接管理
│   │   └── websocket_log_service.py         # 日志推送
│   │
│   ├── monitoring/             # 监控服务
│   │   └── monitoring_service.py
│   │
│   ├── alert/                  # 告警服务
│   │   ├── alert_service.py
│   │   ├── alert_manager.py
│   │   └── alert_channels/     # 告警渠道
│   │
│   ├── audit/                  # 审计服务
│   │   └── audit_service.py
│   │
│   └── system_config/          # 系统配置服务
│       └── system_config_service.py
│
├── grpc_generated/             # gRPC 生成代码（自动生成）
│   ├── common_pb2.py
│   ├── common_pb2_grpc.py
│   ├── node_service_pb2.py
│   └── node_service_pb2_grpc.py
│
├── bootstrap/                  # 启动引导
│   └── ...
│
├── infrastructure/             # 基础设施
│   └── ...
│
├── utils/                      # 工具函数
│   └── ...
│
├── tasks/                      # 任务模块
│   └── antcode_worker/         # Worker 节点（独立部署）
│
├── __init__.py                 # 应用初始化（FastAPI app）
└── main.py                     # 入口文件
```

## 🚀 快速启动

```bash
# 安装依赖
uv sync

# 启动服务
uv run python -m src.main

# 或使用 uvicorn（开发模式）
uv run uvicorn src.asgi:app --reload --host 0.0.0.0 --port 8000
```

## 📖 API 文档

出于安全考虑，默认关闭 Swagger/ReDoc/OpenAPI 路由。

## 🔧 核心功能

### 1. 用户认证

基于 JWT 的认证系统：

```python
# 登录获取 token
POST /api/v1/auth/login
{
    "username": "admin",
    "password": "Admin123!"
}

# 使用 token 访问 API
Authorization: Bearer <token>
```

### 2. 项目管理

支持三种项目类型：
- **代码项目** - 直接编写 Python 代码
- **文件项目** - 上传 Python 文件
- **规则项目** - 配置化爬虫规则

```python
# 创建项目
POST /api/v1/projects/unified
{
    "name": "示例项目",
    "project_type": "code",
    "code_content": "print('Hello')"
}
```

### 3. 任务调度

支持多种调度方式：
- **立即执行** - 一次性任务
- **定时执行** - 指定时间执行
- **周期执行** - 间隔时间重复
- **Cron 表达式** - 灵活的 Cron 配置

```python
# 创建调度任务
POST /api/v1/scheduler/
{
    "project_id": 1,
    "trigger_type": "cron",
    "cron_expression": "0 0 * * *"
}
```

### 4. gRPC 通信

Master 与 Worker 之间的高性能通信：

```protobuf
service NodeService {
    rpc Heartbeat(HeartbeatRequest) returns (HeartbeatResponse);
    rpc ReportTaskStatus(TaskStatusRequest) returns (TaskStatusResponse);
    rpc SendLogs(LogBatchRequest) returns (LogBatchResponse);
}
```

### 5. WebSocket 实时推送

```javascript
// 日志实时推送
ws://localhost:8000/ws/logs/{execution_id}
```

## 📊 数据模型

### 核心模型关系

```
User ──┬── Project ──┬── SchedulerTask ──── TaskLog
       │             │
       │             └── NodeProject ──── Node
       │
       └── AuditLog
```

### 主要模型

| 模型 | 说明 |
|------|------|
| `User` | 用户信息、认证 |
| `Project` | 项目配置、代码 |
| `SchedulerTask` | 调度任务配置 |
| `Node` | Worker 节点信息 |
| `NodeProject` | 节点-项目关联 |
| `TaskLog` | 任务执行日志 |
| `AuditLog` | 操作审计日志 |
| `SystemConfig` | 系统配置项 |

## 🔌 服务层架构

```
┌─────────────────────────────────────────────────────────────┐
│                        API 路由层                           │
│  (api/v1/*.py - 请求验证、响应格式化)                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        业务服务层                           │
│  (services/*_service.py - 业务逻辑、事务管理)                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        数据访问层                           │
│  (models/*.py - Tortoise ORM 模型)                         │
└─────────────────────────────────────────────────────────────┘
```

## 🔧 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_URL` | 数据库连接 | SQLite |
| `REDIS_URL` | Redis 连接 | 内存缓存 |
| `SERVER_HOST` | 服务主机 | 0.0.0.0 |
| `SERVER_PORT` | 服务端口 | 8000 |
| `GRPC_ENABLED` | 启用 gRPC | true |
| `GRPC_PORT` | gRPC 端口 | 50051 |
| `JWT_SECRET` | JWT 密钥 | 自动生成 |
| `LOG_LEVEL` | 日志级别 | INFO |

### 数据库配置

```python
# SQLite（默认）
DATABASE_URL=

# MySQL
DATABASE_URL=mysql://user:pass@localhost:3306/antcode

# PostgreSQL
DATABASE_URL=postgres://user:pass@localhost:5432/antcode
```

## 📝 开发规范

### 代码风格

- 遵循 PEP 8，4 空格缩进
- 补全类型提示
- 优先使用 async/await
- 函数/字段用 snake_case
- 类/枚举用 PascalCase

### 路由规范

```python
from src.core.response import BaseResponse
from src.schemas.xxx import XxxResponse

@router.get("/xxx", response_model=BaseResponse[XxxResponse])
async def get_xxx():
    """接口说明"""
    data = await xxx_service.get_xxx()
    return BaseResponse.success(data=data)
```

### 服务规范

```python
from src.services.base import BaseService

class XxxService(BaseService):
    async def get_xxx(self, id: int) -> XxxModel:
        """获取 xxx"""
        return await XxxModel.get_or_none(id=id)
```

## 🔗 相关文档

- [gRPC 通信](../docs/grpc-communication.md)
- [系统配置](../docs/system-config.md)
- [数据库设置](../docs/database-setup.md)
- [日志 API](../docs/logs-api.md)
