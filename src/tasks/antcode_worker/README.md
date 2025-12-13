# AntCode Worker Node

> 分布式任务执行节点 - 优先级调度架构

Worker 是 AntCode 平台的分布式执行引擎，负责接收 Master 下发的任务并执行。支持 gRPC/HTTP 双协议通信、优先级调度、自适应资源限制等特性。

## ✨ 核心特性

- 🚀 **优先级调度** - 支持 5 级优先级，紧急任务优先执行
- 🔄 **双协议通信** - gRPC 高性能通信，HTTP 自动降级
- 📊 **自适应资源** - 根据系统资源自动调整并发数
- 🐍 **多执行器** - 支持代码执行、爬虫执行等多种任务类型
- 💾 **项目缓存** - 智能缓存项目文件，减少网络传输
- 📝 **批量日志** - 日志批量上报，降低网络开销

## 📁 目录结构

```
antcode_worker/
├── core/                    # 核心层
│   ├── engine.py            # WorkerEngine - 任务调度引擎
│   ├── scheduler.py         # Scheduler - 优先级调度器
│   └── signals.py           # SignalManager - 信号系统
│
├── executors/               # 执行器层
│   ├── base.py              # BaseExecutor - 执行器基类
│   ├── code_executor.py     # CodeExecutor - 代码执行器
│   ├── spider_executor.py   # SpiderExecutor - 爬虫执行器
│   ├── render_executor.py   # RenderExecutor - 渲染执行器
│   └── secure_executor.py   # SecureExecutor - 安全执行器
│
├── transport/               # 传输层（gRPC/HTTP）
│   ├── protocol.py          # 协议抽象接口
│   ├── grpc_client.py       # gRPC 客户端
│   ├── http_client.py       # HTTP 客户端
│   ├── resilient_client.py  # 弹性客户端（重试/降级）
│   └── communication_manager.py  # 统一通信管理
│
├── domain/                  # 领域层
│   ├── models.py            # 数据模型
│   ├── interfaces.py        # 接口定义
│   └── events.py            # 领域事件
│
├── services/                # 业务服务
│   ├── heartbeat_service.py # 心跳服务
│   ├── task_service.py      # 任务服务
│   ├── log_service.py       # 日志服务
│   ├── metrics_service.py   # 指标服务
│   ├── env_service.py       # 环境管理
│   ├── project_service.py   # 项目管理
│   ├── project_cache.py     # 项目缓存
│   ├── resource_monitor.py  # 资源监控
│   ├── log_buffer.py        # 日志缓冲
│   ├── capability_service.py # 能力上报
│   └── master_client.py     # Master 通信
│
├── api/                     # API 层
│   ├── app.py               # FastAPI 应用
│   ├── deps.py              # 依赖注入
│   ├── schemas.py           # 请求/响应模式
│   └── routes/              # 路由模块
│       ├── node.py          # 节点管理
│       ├── queue.py         # 队列管理
│       ├── envs.py          # 环境管理
│       ├── projects.py      # 项目管理
│       ├── tasks.py         # 任务管理
│       └── spider.py        # 爬虫管理
│
├── spider/                  # 爬虫组件
│   ├── base.py              # Spider 基类
│   ├── client.py            # HTTP 客户端
│   ├── render_client.py     # 渲染客户端
│   ├── render_spider.py     # 渲染爬虫
│   ├── selector.py          # CSS/XPath 选择器
│   ├── request.py           # 请求封装
│   ├── middlewares.py       # 中间件
│   └── examples/            # 示例爬虫
│
├── grpc_generated/          # gRPC 生成代码（自动生成）
├── proto/                   # Protocol Buffers 定义
├── utils/                   # 工具函数
│   ├── hash_utils.py        # 哈希工具
│   ├── serialization.py     # 序列化工具
│   └── exceptions.py        # 异常定义
│
├── models/                  # 数据模型
├── scripts/                 # 脚本工具
├── data/                    # 运行时数据
│
├── cli.py                   # 命令行入口
├── config.py                # 配置管理
├── pyproject.toml           # 项目配置
└── __main__.py              # 模块入口
```

## 🚀 快速启动

```bash
# 安装依赖
uv sync

# 生成 gRPC 代码（修改 proto 文件后需要）
uv run python scripts/generate_proto.py

# 交互式菜单
python -m antcode_worker

# 命令行启动
python -m antcode_worker --name Worker-001 --port 8001
```

## 💻 代码使用

### 基础使用

```python
from antcode_worker import WorkerEngine, EngineConfig, Signal

config = EngineConfig(max_concurrent_tasks=5)
engine = WorkerEngine(config)

await engine.start()
await engine.create_task(project_id="xxx", params={}, priority=1)
await engine.stop()
```

### 优先级调度

```python
from antcode_worker import Scheduler, BatchReceiver, TaskItem, BatchTaskRequest, ProjectType

# 创建调度器
scheduler = Scheduler()
await scheduler.start()

# 入队任务（按优先级）
await scheduler.enqueue("task-1", "proj-1", ProjectType.RULE, priority=0)  # 最高
await scheduler.enqueue("task-2", "proj-2", ProjectType.CODE, priority=2)  # 普通

# 出队（按优先级顺序）
task = await scheduler.dequeue()  # 返回 task-1

# 批量接收
receiver = BatchReceiver(scheduler)
tasks = [TaskItem(task_id="t1", project_id="p1", project_type=ProjectType.CODE)]
response = await receiver.receive_batch(BatchTaskRequest(tasks=tasks, node_id="node-1"))
```

### 优先级说明

| 优先级 | 值 | 说明 |
|--------|-----|------|
| CRITICAL | 0 | 紧急任务 |
| HIGH | 1 | 高优先级（规则项目默认） |
| NORMAL | 2 | 普通优先级（代码/文件项目默认） |
| LOW | 3 | 低优先级 |
| IDLE | 4 | 空闲时执行 |

## 📖 API 文档

启动后访问: `http://localhost:8001/docs`

### 队列管理 API

- `POST /queue/batch` - 批量接收任务
- `GET /queue/status` - 获取队列状态
- `GET /queue/details` - 获取队列详情
- `PUT /queue/tasks/{task_id}/priority` - 更新任务优先级
- `DELETE /queue/tasks/{task_id}` - 取消任务

## 🔧 架构分层

| 层 | 职责 |
|---|------|
| **Core** | 引擎、调度器、信号系统 |
| **Executors** | 代码执行、爬虫执行 |
| **Transport** | gRPC/HTTP 通信、协议切换 |
| **Domain** | 数据模型、接口定义 |
| **API** | HTTP 接口、请求验证 |
| **Services** | 环境管理、项目管理 |
| **Spider** | 爬虫组件 |

## 🔄 数据流

```
Master 批量下发任务
        ↓
BatchReceiver 接收验证
        ↓
Scheduler 优先级入队
        ↓
WorkerEngine 调度执行
        ↓
CodeExecutor/SpiderExecutor 执行
        ↓
结果回调 → Master
```

## 📊 自适应资源限制

Worker 支持根据系统资源自动计算和动态调整任务限制。

### 自动计算规则

| 参数 | 计算公式 | 范围 |
|------|----------|------|
| `max_concurrent_tasks` | `min(CPU核心数, 可用内存GB/2, 10)` | 1-10 |
| `task_memory_limit_mb` | `总内存70% / 并发数` | 512-4096 MB |
| `task_cpu_time_limit_sec` | `min(1800, max(300, CPU核心数*60))` | 300-1800 秒 |

### 动态调整策略

- **CPU > 85%**: 自动减少并发数
- **CPU < 50%**: 自动增加并发数（不超过上限）
- **内存 > 80%**: 降低单任务内存限制

### 环境变量覆盖

```bash
# 手动设置并发数
export WORKER_MAX_CONCURRENT_TASKS=5

# 手动设置内存限制
export WORKER_TASK_MEMORY_LIMIT_MB=1024

# 手动设置 CPU 时间限制
export WORKER_TASK_CPU_TIME_LIMIT_SEC=600
```

### 资源管理 API

```bash
# 获取当前资源限制和监控状态
GET /node/resources

# 手动调整资源限制
POST /node/resources?max_concurrent_tasks=5&auto_resource_limit=false
```

### 配置项

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `auto_resource_limit` | `true` | 启用自适应资源限制 |
| `max_concurrent_tasks` | `0` | 最大并发数（0=自动） |
| `task_memory_limit_mb` | `0` | 单任务内存限制（0=自动） |
| `task_cpu_time_limit_sec` | `0` | 单任务 CPU 时间限制（0=自动） |


## 🔌 gRPC 通信

Worker 支持 gRPC 作为与 Master 通信的高性能协议。

### 配置

在 `node_config.yaml` 中配置：

```yaml
# gRPC 配置
grpc_enabled: true           # 是否启用 gRPC
grpc_port: 50051             # Master gRPC 端口
prefer_grpc: true            # 优先使用 gRPC
grpc_reconnect_base_delay: 5.0   # 重连基础延迟（秒）
grpc_reconnect_max_delay: 60.0   # 重连最大延迟（秒）
```

### 协议选择策略

1. `prefer_grpc=true`（默认）：优先 gRPC，失败自动降级到 HTTP
2. `prefer_grpc=false`：始终使用 HTTP

### 生成 gRPC 代码

修改 `proto/` 目录下的 `.proto` 文件后，需要重新生成代码：

```bash
uv run python scripts/generate_proto.py
```

### Proto 文件说明

| 文件 | 说明 |
|------|------|
| `proto/common.proto` | 通用消息类型（Timestamp, Metrics, OSInfo） |
| `proto/node_service.proto` | 节点服务定义（NodeService, 所有消息类型） |

**注意**: Proto 文件需要与 Master 端保持同步，确保消息格式一致。
