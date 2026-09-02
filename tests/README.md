# AntCode 测试目录

本目录包含 AntCode 项目的所有测试代码。

## 目录结构

```
tests/
├── boundary/              # 边界与属性测试（导入边界、分层边界、术语一致性）
├── contracts/             # Worker transport 跨实现契约（需真 Redis，见该目录 README）
├── e2e/                   # 端到端测试
├── integration/           # 集成测试
│   ├── crawl/
│   ├── gateway/
│   ├── postgres/
│   └── worker/
├── loadtest/              # 压力测试（默认惰性，见该目录 README）
└── unit/                  # 单元测试
    ├── core/              # antcode_core 包测试
    ├── gateway/           # Gateway 服务测试
    ├── master/            # Master 服务测试
    ├── scrapy/            # antcode_scrapy 包测试
    ├── scripts/           # scripts/ 下工具脚本测试
    ├── web_api/           # Web API 服务测试
    └── worker/            # Worker 服务测试
```

## 测试类型

### 单元测试 (`tests/unit/`)

按服务/包组织，不依赖外部中间件。

### 集成测试 (`tests/integration/`)

组件间交互测试，需要真 Redis + PostgreSQL。

### 契约测试 (`tests/contracts/`)

`TransportBase` 的 Redis Direct 与 Gateway 两个实现共享同一套断言，需要真 Redis。

### 边界测试 (`tests/boundary/`)

服务边界和导入规则验证测试。

### 端到端测试 (`tests/e2e/`)

跨服务的端到端测试，需要完整的基础设施（PostgreSQL、Redis、Git 服务）。

### 压力测试 (`tests/loadtest/`)

性能和压力测试，用于验证系统在高负载下的表现。

## 运行测试

裸 `pytest`（= `make test`）只跑 `testpaths` 声明的 `tests/unit` + `tests/boundary`。
**不要跑 `pytest tests/`** —— 它会连 `tests/e2e` 一起收集，而 e2e 需要完整容器栈，
在开发机上必失败。其余套件各自需要真实依赖，按下面的小节单独执行。

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

集成测试会 `SET` Master 代际镜像 `{ns}:fencing:dispatch:master`、改写
Lease/heartbeat/ready stream，并对实例执行 `ACL SETUSER/DELUSER`。指向活栈会让
在任 Master 永久无法派发且不自愈，所以目标 Redis 必须先被显式声明为**一次性实例**
（`tests/integration/conftest.py` 会 fail-closed 校验，缺标记直接终止整轮 run）。
`ACL` 是实例级配置、`test_redis_acl_live.py` 又把 `antcode` namespace 写死，因此
换 `REDIS_NAMESPACE` 或换 db 号都不构成隔离，只能换实例。

```bash
# 集成测试需要真实 Redis，以及两个职责隔离的 PostgreSQL 测试数据库。
export ANTCODE_INTEGRATION_REDIS_URL=redis://127.0.0.1:16379/14
# test_fault_tolerance.py 驱动真实 ResultLoop，走的是生产变量 REDIS_URL，
# 因此这两个 URL 指向的实例都必须带一次性实例标记。
export REDIS_URL="$ANTCODE_INTEGRATION_REDIS_URL"
export TEST_DATABASE_URL=postgresql://antcode:password@127.0.0.1:15432/antcode_migration_test
export DATABASE_URL=postgresql://antcode:password@127.0.0.1:15432/antcode_e2e_test

# 只在确认该 Redis 可被销毁之后执行；严禁写到生产或共享栈的 Redis 上。
redis-cli -u "$ANTCODE_INTEGRATION_REDIS_URL" \
  SET antcode:integration-test:disposable-binding ANTCODE_INTEGRATION_TESTS_MAY_DESTROY_THIS_REDIS

uv run pytest tests/integration/ -v
```

### 运行边界测试

```bash
uv run pytest tests/boundary/
```

### 运行契约测试

需要一次性 Redis（默认 `localhost:16379` DB 14）；起停方式与文件组织见
[`tests/contracts/README.md`](contracts/README.md)。

```bash
uv run pytest tests/contracts/ -v
```

### 运行端到端测试

```bash
# FULL E2E 只通过公开 API 验证预先 bootstrap 的管理员，不接收数据库凭据，
# 也不会创建、提权或重置管理员。远程环境必须使用 HTTPS。
export ANTCODE_E2E_CONFIRM=FULL
export ANTCODE_E2E_WORKER_ID=worker-e2e-001
export ANTCODE_E2E_ADMIN_USER=admin
export ANTCODE_E2E_ADMIN_PASSWORD='<bootstrap 时使用的一次性管理员口令>'
export ANTCODE_E2E_WEB_API_URL=https://antcode-test.example.com
export ANTCODE_E2E_EXPECT_TRANSPORT_MODE=direct
export ANTCODE_E2E_GIT_ROOT=/srv/antcode-e2e-git
export ANTCODE_E2E_GIT_BASE_URL=http://git.antcode-test.example.com:18081
# Spider 数据场景只用专用 Redis 连接做严格清理；不会加载 DATABASE_URL。
export ANTCODE_E2E_REDIS_URL='<专用 E2E Redis URL>'
export ANTCODE_E2E_REDIS_NAMESPACE=antcode

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
testpaths = ["tests/unit", "tests/boundary"]
filterwarnings = [
    "ignore:'crypt' is deprecated:DeprecationWarning",
    "error::pytest.PytestUnraisableExceptionWarning",
]
markers = [
    "integration: 集成测试标记",
    "e2e: 需要显式确认和专用测试环境的端到端测试",
    "pbt: 边界/属性测试标记",
    "transport(mode): Worker transport implementation covered by a contract test",
    "loadtest_scenario: guarded external load-test scenario",
    "loadtest_write: load-test scenario that creates or triggers tasks",
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
