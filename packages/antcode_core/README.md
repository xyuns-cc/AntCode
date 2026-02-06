# AntCode Core

AntCode 共享核心包，提供所有服务共用的基础功能。

## 模块结构

```
antcode_core/
├── common/           # 通用模块
│   ├── config.py     # 配置管理
│   ├── logging.py    # 日志配置
│   ├── exceptions.py # 异常定义
│   ├── ids.py        # ID 生成
│   ├── time.py       # 时间工具
│   └── security/     # 安全相关
│       ├── jwt.py
│       ├── api_key.py
│       ├── mtls.py
│       └── permissions.py
│
├── infrastructure/   # 基础设施适配
│   ├── db/           # 数据库
│   │   └── tortoise.py
│   ├── redis/        # Redis
│   │   ├── client.py
│   │   ├── keys.py
│   │   ├── streams.py
│   │   ├── zsets.py
│   │   └── locks.py
│   ├── storage/      # 对象存储
│   │   ├── base.py
│   │   ├── s3.py
│   │   ├── local.py
│   │   └── presign.py
│   └── observability/ # 可观测性
│       ├── metrics.py
│       ├── health.py
│       └── tracing.py
│
└── domain/           # 领域层
    ├── models/       # 数据库模型
    ├── schemas/      # Pydantic Schema
    └── services/     # 纯业务服务
```

## 使用方式

```python
from antcode_core.common import settings, setup_logging
from antcode_core.common.exceptions import NotFoundError
from antcode_core.common.ids import generate_run_id
from antcode_core.infrastructure.redis import RedisClient
from antcode_core.domain.models import Task, Worker
```

## 设计原则

1. **无 HTTP/gRPC/WS**: domain/services 只包含纯业务逻辑
2. **单一职责**: 每个模块职责明确
3. **依赖边界**: 服务只能从 antcode_core 导入共享功能
