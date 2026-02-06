# AntCode 测试目录

本目录包含 AntCode 项目的所有测试代码。

## 目录结构

```
tests/
├── boundary/              # 边界与属性测试
│   ├── test_import_boundary.py     # 导入边界测试
│   ├── test_log_completeness.py    # 日志完整性测试
│   └── test_service_boundary.py    # 服务边界测试
├── e2e/                   # 端到端测试
│   ├── test_task_lifecycle.py      # 任务生命周期测试
│   ├── test_worker_lifecycle.py    # Worker 生命周期测试
│   └── test_log_streaming.py       # 日志流测试
├── integration/           # 集成测试
│   └── worker/            # Worker 集成测试
│       ├── test_direct_mode_e2e.py
│       ├── test_gateway_mode_e2e.py
│       ├── test_worker_integration.py
│       └── ...
├── loadtest/              # 压力测试
│   ├── test_task_throughput.py     # 任务吞吐量测试
│   ├── test_worker_scalability.py  # Worker 可扩展性测试
│   └── test_log_throughput.py      # 日志吞吐量测试
└── unit/                  # 单元测试
    ├── core/              # antcode_core 包测试
    ├── gateway/           # Gateway 服务测试
    ├── master/            # Master 服务测试
    ├── web_api/           # Web API 服务测试
    ├── worker/            # Worker 服务测试
    └── test_*.py          # 通用单元测试
```

## 测试类型

### 单元测试 (`tests/unit/`)

按服务/包组织的单元测试：
- `tests/unit/core/` - antcode_core 包测试
- `tests/unit/gateway/` - Gateway 服务测试
- `tests/unit/master/` - Master 服务测试
- `tests/unit/web_api/` - Web API 服务测试
- `tests/unit/worker/` - Worker 服务测试

### 集成测试 (`tests/integration/`)

组件间交互测试：
- `tests/integration/worker/` - Worker 集成测试（Direct/Gateway 模式）

### 边界测试 (`tests/boundary/`)

服务边界和导入规则验证测试。

### 端到端测试 (`tests/e2e/`)

跨服务的端到端测试，需要完整的基础设施（MySQL、Redis、MinIO）。

### 压力测试 (`tests/loadtest/`)

性能和压力测试，用于验证系统在高负载下的表现。

## 运行测试

### 运行所有测试

```bash
uv run pytest tests/
```

### 运行单元测试

```bash
uv run pytest tests/unit/
```

### 运行特定服务的单元测试

```bash
# Worker 单元测试
uv run pytest tests/unit/worker/

# Gateway 单元测试
uv run pytest tests/unit/gateway/

# Master 单元测试
uv run pytest tests/unit/master/

# Web API 单元测试
uv run pytest tests/unit/web_api/

# Core 包单元测试
uv run pytest tests/unit/core/
```

### 运行集成测试

```bash
# Worker 集成测试
uv run pytest tests/integration/worker/ -m integration
```

### 运行边界测试

```bash
uv run pytest tests/boundary/
```

### 运行端到端测试

```bash
# 需要先启动基础设施
docker compose -f infra/docker/docker-compose.dev.yml up -d mysql redis minio

# 运行 E2E 测试
uv run pytest tests/e2e/ -v
```

### 运行压力测试

```bash
# 压力测试默认跳过，需要手动启用
uv run pytest tests/loadtest/ -v --run-loadtest
```

## 测试配置

### 环境变量

测试可以通过环境变量配置：

```bash
# 数据库配置
TEST_MYSQL_HOST=localhost
TEST_MYSQL_PORT=3306
TEST_MYSQL_USER=test
TEST_MYSQL_PASSWORD=test
TEST_MYSQL_DATABASE=antcode_test

# Redis 配置
TEST_REDIS_HOST=localhost
TEST_REDIS_PORT=6379
TEST_REDIS_DB=15

# MinIO 配置
TEST_MINIO_ENDPOINT=localhost:9000
TEST_MINIO_ACCESS_KEY=minioadmin
TEST_MINIO_SECRET_KEY=minioadmin
TEST_MINIO_BUCKET=antcode-test
```

### pytest 配置

项目根目录的 `pyproject.toml` 包含 pytest 配置：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
asyncio_mode = "auto"
```

## 编写测试指南

### 单元测试

- 测试单个函数或类
- 不依赖外部服务
- 使用 mock 隔离依赖
- 放在 `tests/unit/<service>/` 目录

### 集成测试

- 测试组件间的交互
- 可能需要本地 Docker 服务
- 放在 `tests/integration/<service>/` 目录

### 端到端测试

- 测试完整的业务流程
- 需要完整的基础设施
- 放在 `tests/e2e/` 目录

### 压力测试

- 测试系统性能和稳定性
- 需要专门的测试环境
- 放在 `tests/loadtest/` 目录
