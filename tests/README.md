# AntCode 测试目录

本目录包含 AntCode 项目的所有测试代码。

## 目录结构

```
tests/
├── boundary/              # 边界与属性测试
│   ├── test_import_boundary.py     # 导入边界测试
│   └── test_service_boundary.py    # 服务边界测试
├── e2e/                   # 端到端测试
│   ├── test_task_lifecycle.py      # 任务生命周期测试
│   ├── test_worker_lifecycle.py    # Worker 生命周期测试
│   └── test_log_streaming.py       # 日志流测试
├── integration/           # 集成测试
│   └── worker/            # Worker 集成测试
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

跨服务的端到端测试，需要完整的基础设施（PostgreSQL、Redis、Git 服务）。

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
# 集成测试需要真实 Redis，以及两个职责隔离的 PostgreSQL 测试数据库。
export ANTCODE_INTEGRATION_REDIS_URL=redis://127.0.0.1:16379/14
export TEST_DATABASE_URL=postgresql://antcode:password@127.0.0.1:15432/antcode_migration_test
export DATABASE_URL=postgresql://antcode:password@127.0.0.1:15432/antcode_e2e_test

uv run pytest tests/integration/ -v
```

### 运行边界测试

```bash
uv run pytest tests/boundary/
```

### 运行端到端测试

```bash
# FULL E2E 必须显式绑定专用 PostgreSQL host、库名和 dedicated Worker。
export ANTCODE_E2E_CONFIRM=FULL
export DATABASE_URL=postgresql://antcode:password@127.0.0.1:15433/antcode_e2e_test
export ANTCODE_E2E_DATABASE_HOST=127.0.0.1
export ANTCODE_E2E_DATABASE_NAME=antcode_e2e_test
export ANTCODE_E2E_WORKER_ID=worker-e2e-001
export ANTCODE_E2E_WEB_API_URL=http://192.168.1.250:8000
export ANTCODE_E2E_EXPECT_TRANSPORT_MODE=direct
export ANTCODE_E2E_GIT_ROOT=/srv/antcode-e2e-git
export ANTCODE_E2E_GIT_BASE_URL=http://192.168.1.250:18081

# 运行 E2E 测试
uv run pytest tests/e2e/ -v
```

### 运行压力测试

```bash
# 外部压测必须显式确认；完整写场景使用 FULL。
ANTCODE_LOADTEST_CONFIRM=FULL \
  uv run pytest tests/loadtest/ -v --run-loadtests
```

## 测试配置

### 环境变量

真实集成测试使用以下环境变量：

```bash
ANTCODE_CONTRACT_REDIS_URL=redis://127.0.0.1:16379/14
ANTCODE_INTEGRATION_REDIS_URL=redis://127.0.0.1:16379/14
TEST_DATABASE_URL=postgresql://antcode:password@127.0.0.1:15432/antcode_migration_test
DATABASE_URL=postgresql://antcode:password@127.0.0.1:15432/antcode_e2e_test
```

### pytest 配置

项目根目录的 `pyproject.toml` 包含 pytest 配置：

```toml
[tool.pytest.ini_options]
asyncio_mode = "strict"
addopts = "--strict-markers"
pythonpath = ["."]
markers = [
    "integration: 集成测试标记",
    "e2e: 端到端测试标记",
    "pbt: 边界/属性测试标记",
    "transport(mode): 传输合同覆盖模式",
    "loadtest_scenario: 外部压测场景",
    "loadtest_write: 会写入数据的压测场景",
]
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
