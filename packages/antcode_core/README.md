# AntCode Core

AntCode 共享核心包，提供所有服务共用的基础功能。

## 模块结构

```
antcode_core/
├── common/              # 配置、日志、异常、序列化、安全
│   ├── config.py
│   ├── logging.py
│   ├── exceptions.py
│   └── security/        # auth(JWT) / api_key / permissions / worker_auth /
│                        # encrypted_fields / login_guard / redis_acl ...
│
├── application/services/  # 应用服务（scheduler / workers / projects / logs /
│                          # crawl / users / security / alert / audit ...）
│
├── domain/              # 领域层
│   ├── models/          # Tortoise ORM 模型
│   └── schemas/         # Pydantic Schema
│
├── infrastructure/      # 基础设施适配
│   ├── db/tortoise.py
│   ├── redis/           # client / control_plane(key 规范) / stream_* / locks ...
│   ├── postgres/        # blob artifact store
│   ├── cache/
│   └── resilience/      # circuit_breaker / health
│
├── observability/tracing.py
└── spider_*.py          # spider 数据面 ingest / writer / retention / write fence
```

## 使用方式

**不做聚合再导出**，一律直接导入具体子模块——在 `common/__init__.py` 里
re-export `settings` 会让任何 `import antcode_core.common.<子模块>` 都连带
实例化控制面 `Settings()`，Rule 沙箱 relay 与只依赖 Redis 的清理服务会直接导入失败。

```python
from antcode_core.common.config import settings
from antcode_core.common.logging import setup_logging
from antcode_core.common.exceptions import NotFoundError
from antcode_core.infrastructure.redis import get_redis_client
from antcode_core.domain.models import Task, Worker
```

## 设计原则

1. **无 HTTP/gRPC/WS**: domain 与 application 只包含纯业务逻辑
2. **单一职责**: 每个模块职责明确
3. **依赖边界**: 服务只能从 antcode_core 导入共享功能
