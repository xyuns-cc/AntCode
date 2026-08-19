# migrations/models

首次发版（v1.0.0）不带任何迁移文件——**model 定义是唯一真源**。

## 全新部署

```bash
uv run python scripts/init_db.py
```

会用 `Tortoise.generate_schemas(safe=True)` 从 model 直接建表，然后补建
几个性能关键的索引，并创建默认管理员。所需环境变量与建表清单分别见
`scripts/init_db_environment.py` 与 `scripts/init_db.py` 的 `REQUIRED_TABLES`。

## 后续 schema 变更

如果版本升级涉及 model 结构调整，选一种：

**A. 手写 SQL 补丁**（推荐用于点状小改）

```
migrations/models/2026xxxx_add_foo.sql
```

在 release notes 中说明"升级前请执行 XXX.sql"。

**B. 引入 aerich 常规迁移链**

```bash
uv run aerich init -t antcode_core.infrastructure.db.tortoise.TORTOISE_ORM
uv run aerich init-db
# 后续每次改 model
uv run aerich migrate --name change_foo
uv run aerich upgrade
```

选 B 时把这份 README 换成 aerich 生成的 `_aerich.py` 记录表和迁移文件。
