# 数据库配置与迁移

## 默认策略

- 优先读取 `DATABASE_URL`
- 未配置时使用默认 SQLite：`data/backend/db/antcode.sqlite3`
- 生产环境建议 MySQL 或 PostgreSQL

## 连接示例

### SQLite（默认）

```env
DATABASE_URL=
# 或显式指定
# DATABASE_URL=sqlite:///./data/backend/db/antcode.sqlite3
```

### MySQL

```env
DATABASE_URL=mysql+asyncmy://user:password@host:3306/antcode
```

### PostgreSQL

```env
DATABASE_URL=postgresql://user:password@host:5432/antcode
```

## 迁移工具（Aerich）

常用命令：

```bash
# 初始化（首次）
uv run python scripts/db_migrate.py init

# 生成迁移
uv run python scripts/db_migrate.py migrate --name "add_xxx"

# 应用迁移
uv run python scripts/db_migrate.py upgrade

# 回滚一步
uv run python scripts/db_migrate.py downgrade

# 查看历史
uv run python scripts/db_migrate.py history
```

## 生产迁移流程（建议）

1. 先备份数据库
2. 在预发布环境执行 `upgrade`
3. 观察关键接口与任务链路
4. 再在生产执行同版本迁移

## Docker 场景

在容器启动链中先执行迁移，再启动服务：

```bash
python scripts/db_migrate.py upgrade && python -m antcode_web_api
```

## 排障要点

- SQLite 路径异常：确认 `data/backend/db` 可写
- MySQL 连接失败：检查网络、账号权限、字符集
- 迁移冲突：先核对 `migrations/models/` 与当前模型版本
