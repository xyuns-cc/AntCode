# 数据库初始化

首次发版（v1.0.0）**不带 aerich 迁移链**，全部走 model → schema 一键生成。

## 前置

- **PostgreSQL 14 或以上**
- 一个**空数据库**（推荐名字 `AntCode`）
- 数据库用户拥有 `CREATE TABLE / CREATE INDEX / INSERT` 权限

```sql
-- 参考建库脚本
CREATE ROLE antcode WITH LOGIN PASSWORD 'change-me';
CREATE DATABASE "AntCode" OWNER antcode;
GRANT ALL PRIVILEGES ON DATABASE "AntCode" TO antcode;
```

## 一键初始化

`.env` 里至少配好：

```env
DATABASE_URL=postgresql://antcode:change-me@127.0.0.1:5432/AntCode
REDIS_URL=redis://:redis-pass@127.0.0.1:6379/0
ENCRYPTION_KEY=<openssl rand -base64 48>
JWT_SECRET=<openssl rand -base64 48>
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=<强口令>
```

然后：

```bash
uv run python scripts/init_db.py
```

脚本会做四件事：

1. **建表** — `Tortoise.generate_schemas(safe=True)` 从 `antcode_core.domain.models` 反射所有 model，一次性建齐 30+ 张表和主键索引。`safe=True` 保证幂等（已存在的表跳过）。
2. **补性能索引** — model 里没声明但热路径依赖的两个索引：
   - `idx_task_executions_crawl_batch_id` — `task_executions.result_data->>'crawl_batch_id'` 的 partial functional index，crawl_batch_status_loop / test_service / web_api 批次聚合和导出都靠它
   - `idx_crawl_batches_status_created` — `crawl_batches (status, created_at DESC)` 复合索引，批次列表页排序用
3. **初始化系统配置** — 写入默认告警配置、调度参数、日志保留策略等（后续可通过管理界面调整）
4. **创建默认管理员** — 用 `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` 建 super_admin 用户。如果 `DEFAULT_ADMIN_PASSWORD` 为空，跳过此步骤（首次启动 web_api 时也会尝试创建）。

## 幂等重跑

`init_db.py` 可以随时重复执行：
- 表已存在 → 跳过
- 索引已存在 → `IF NOT EXISTS` 兜住
- 管理员已存在 → 跳过创建（**不会**重置密码）

如果要**重置管理员密码**，直接用 REPL：

```python
import asyncio
from antcode_core.infrastructure.db.tortoise import init_db, close_db
from antcode_core.domain.models.user import User

async def reset():
    await init_db(service="web_api")
    u = await User.get_or_none(username="admin")
    u.set_password("new-strong-password")
    await u.save()
    await close_db()

asyncio.run(reset())
```

## 后续 schema 变更

发版后如果需要改 model schema，两种路径：

### 方案 A：手写 SQL 补丁（推荐用于点状小改）

在 `migrations/models/` 下建 `20261120_add_foo_column.sql`：

```sql
ALTER TABLE "projects" ADD COLUMN IF NOT EXISTS "foo" VARCHAR(64);
CREATE INDEX IF NOT EXISTS "idx_projects_foo" ON "projects" ("foo");
```

Release note 里说明"升级前请执行 `psql -f migrations/models/20261120_add_foo_column.sql`"。

优点：直观，运维一眼看懂。缺点：多环境同步需要人工纪律。

### 方案 B：引入 aerich 迁移链

```bash
uv run aerich init -t antcode_core.infrastructure.db.tortoise.TORTOISE_ORM
uv run aerich init-db
# 后续每次改 model
uv run aerich migrate --name change_foo
uv run aerich upgrade
```

优点：可回溯、可自动 diff。缺点：初次接入需要把 v1.0.0 的表 schema 生成一个 baseline 迁移。

选定方案后请更新本文档相应部分。

## 生产环境 note

- **大表加索引会锁表**。用 `CREATE INDEX CONCURRENTLY` 手工建（不能包在事务里，aerich 里也不行）：
  ```sql
  CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_foo ON large_table (foo);
  ```
- **备份**：至少 `pg_dump` 每日 cron + 保留 7 天。生产强烈建议开 WAL 归档。
- **连接数**：`.env` 里 `DB_POOL_MAX_WEB_API` / `DB_POOL_MAX_MASTER` / `DB_POOL_MAX_WORKER` 分别控制。默认合计约 60-80 个连接。PG `max_connections` 至少留 2x 冗余。
- **保留策略**：`task_logs` / `audit_logs` / `worker_events` 由 master 的 `log_cleanup_service` 定期清理，参见 `.env` 里 `TASK_LOG_RETENTION_DAYS` / `AUDIT_LOG_RETENTION_DAYS` / `WORKER_EVENT_RETENTION_DAYS`。

## 常见问题

**Q: `init_db.py` 报 "DATABASE_URL 缺少 password"**
A: `.env` 里 DATABASE_URL 必须完整：`postgresql://user:pass@host:port/db`。

**Q: `duplicate key value violates unique constraint "users_username_key"`**
A: 数据库里已经有同名 admin，脚本会自动跳过创建。看到这条错说明是并发执行了两次，重跑一次即可。

**Q: 想清空数据库重来**
A: `DROP DATABASE "AntCode"; CREATE DATABASE "AntCode" OWNER antcode;` 然后重跑 `init_db.py`。⚠️ 会丢所有数据。
