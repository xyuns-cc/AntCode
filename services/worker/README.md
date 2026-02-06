# AntCode Worker 执行器服务

AntCode Worker 是 Execution Plane 的核心组件，负责任务执行、运行时管理、日志输出和心跳上报。

## 架构

Worker 支持两种传输模式：

- **Direct 模式**：内网 Worker 直连 Redis Streams
- **Gateway 模式**：公网 Worker 通过 Gateway gRPC/TLS 连接

## 目录结构

```
src/antcode_worker/
├── __init__.py
├── __main__.py          # CLI 入口
├── cli.py               # 命令行处理
├── config.py            # 配置管理
├── transport/           # 传输层
│   ├── base.py          # 抽象接口
│   ├── redis.py         # Direct 模式
│   └── gateway.py       # Gateway 模式
├── runtime/             # 运行时管理
│   ├── uv_manager.py    # uv 环境管理
│   └── cache_gc.py      # 缓存回收
├── executor/            # 执行器
│   ├── process_executor.py
│   └── sandbox.py       # 可选沙箱
├── logging/             # 日志模块
│   ├── streamer.py      # 实时日志流
│   └── archiver.py      # 日志归档
└── heartbeat/           # 心跳模块
    └── reporter.py      # 心跳上报
```

## 运行

### 交互式菜单

```bash
uv run python -m antcode_worker
```

### 命令行启动

```bash
uv run python -m antcode_worker --name "Worker-001" --port 8001
```

### 使用安装 Key 启动（推荐）

```bash
ANTCODE_WORKER_KEY=your-key ANTCODE_API_BASE_URL=http://localhost:8000 uv run python -m antcode_worker --name "Worker-001" --port 8001
```

### Direct 模式启动（自动注册）

```bash
ANTCODE_API_BASE_URL=http://localhost:8000 \
  uv run python -m antcode_worker --name "Worker-001" --port 8001 --transport direct
```

## 配置

Worker 支持以下配置方式（优先级从高到低）：

1. 命令行参数
2. 配置文件 `worker_config.yaml`
3. 环境变量
4. 默认值

### 主要配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `--name` | 节点名称 | Worker-Node |
| `--port` | 健康检查端口 | 8001 |
| `--region` | 区域标签 | 默认 |
| `--transport` | 传输模式 (direct/gateway) | gateway |

## 健康检查

Worker 提供以下健康检查端点：

- `GET /health` - 基本状态
- `GET /health/live` - 存活探针 (K8s liveness)
- `GET /health/ready` - 就绪探针 (K8s readiness)

## 依赖

- `antcode-core`: 共享核心包
- `antcode-contracts`: gRPC 契约包
