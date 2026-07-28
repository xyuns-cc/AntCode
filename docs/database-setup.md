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
# 不启用 TLS 的本机开发连接
DATABASE_URL=postgresql://antcode:change-me@127.0.0.1:5432/AntCode
# 生产 TLS 示例（CA 路径必须对应用进程/容器可见）
# DATABASE_URL=postgresql://antcode:change-me@db:5432/AntCode?sslmode=verify-full&sslrootcert=/etc/antcode/tls/postgres-ca.crt
REDIS_URL=redis://:redis-pass@127.0.0.1:6379/0
ENCRYPTION_KEY=<openssl rand -base64 48>
ENCRYPTION_KEY_SALT=<openssl rand -hex 16>
# 二选一：inline 或文件。生产 Compose 使用文件挂载。
JWT_SECRET=<openssl rand -base64 48>
# JWT_SECRET_FILE=/run/secrets/jwt_secret
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

## 升级旧版 Worker 安装 Key

从存储明文安装 Key 的版本升级时，必须在启动新版 Web API 前执行：

```bash
psql "$DATABASE_URL" -f migrations/models/20260713_add_worker_install_key_allowed_source.sql
uv run python scripts/migrate_worker_install_keys.py
```

该脚本必须在所有 AntCode Web API、Gateway、Master 与 Worker 进程停止后执行，
避免旧进程在迁移期间继续读写明文键。它会按以下顺序执行两步迁移：

1. 先用 `SCAN + TYPE/DUMP/PTTL/RESTORE/DEL` 把 Redis 中旧的
   `meta/claim/nonce/fail/block` 明文键搬到固定长度哈希键。该流程不使用跨 slot
   的 `RENAME/RENAMENX`，兼容 Redis Cluster；写目标时会扣除快照后的迁移耗时，
   保留永久键语义，并且不会延长或复活已经过期的键；
2. Redis 全部收敛后，把已过期的 `pending` Key 标为 `expired`；再从 Redis meta 恢复仍有效
   Key 的来源限制，仅接受 IP/CIDR，并与 Key 哈希一起在单个数据库事务中
   写入 PostgreSQL。

已是 64 位 SHA-256 且已具备权威来源信息的数据库记录会跳过；已迁移的
Redis 键也不会再次改名，因此可以安全重跑。数据库迁移失败会整体回滚；
此时 Redis 已完成的搬迁会被保留，重跑时数据库仍能从摘要 meta 键恢复来源。
若脚本在 Redis `RESTORE` 后、删除源键前中断，重跑会校验两端 DUMP 内容：
内容相同则按源键剩余 TTL 收敛并删除源键，内容不同则显式报冲突且保留两端，
不会静默覆盖。修复冲突后重跑即可。脚本只扫描当前 `REDIS_NAMESPACE`，升级时必须保持它与
旧部署一致。不要在同一数据库或 Redis 上并发执行多个迁移进程。

若某个 pending Key 的 Redis meta 已丢失，或历史来源配置是 hostname，脚本
会拒绝继续：系统无法安全判断原限制，必须先撤销该 Key 并生成新的 IP/CIDR
来源 Key。新版运行时只以 PostgreSQL `allowed_source` 为权威，Redis meta
缺失不会再放宽来源限制。

## 既有集群升级：按序执行以下迁移

老库（非全新部署）跑新代码时，`generate_schemas(safe=True)` 只补缺失的**表**、
不给已存在的表加列。`init_db.py` 的 `_upgrade_legacy_schema` 已能幂等自愈**新增列**
（task_executions.lease_id / scheduler_outbox.consume_* /
worker_install_keys.registration_*/recovery_*/allowed_source，以及 workers 的
`api_key_previous_expires_at` 等凭据列），启动即补；
但**索引与数据回填**仍需按序手工执行下列迁移（升级新版 Web API 前）：

```bash
# 1. Worker 凭据 hash、轮换宽限期列与查询索引；随后强制回填并删除明文列
psql "$DATABASE_URL" -f migrations/models/20260710_secure_worker_credentials.sql
uv run python scripts/migrate_worker_credentials.py
# 2. task_executions 租约列 + 并发建索引（CONCURRENTLY，不在事务内）
psql "$DATABASE_URL" -f migrations/models/20260713_add_task_run_lease_id.sql
# 3. scheduler_outbox 消费列 + 并发建索引
psql "$DATABASE_URL" -f migrations/models/20260713_add_scheduler_outbox_consumption.sql
# 4. worker_install_keys.allowed_source（来源限制列）
psql "$DATABASE_URL" -f migrations/models/20260713_add_worker_install_key_allowed_source.sql
# 5. task_logs SSE 回放游标索引（CONCURRENTLY，不在事务内）
psql "$DATABASE_URL" -f migrations/models/20260717_add_task_logs_run_id_id_index.sql
# 6. worker_install_keys 注册回收列 + 唯一/部分索引
psql "$DATABASE_URL" -f migrations/models/20260717_add_worker_registration_recovery.sql
# 7. scheduler_outbox.consume_attempts（消费侧重投计数列，ORM 强依赖；纯加列事务迁移）
psql "$DATABASE_URL" -f migrations/models/20260720_add_scheduler_outbox_consume_attempts.sql
# 8. task_executions.lease_gen（P1-GW-04：Lease 代际单调 CAS 列，纯加列事务迁移）
psql "$DATABASE_URL" -f migrations/models/20260722_add_task_run_lease_gen.sql
# 9. TaskRun Lease 代际历史与日志 backlog cutoff（纯建表事务迁移）
psql "$DATABASE_URL" -f migrations/models/20260727_add_task_run_lease_generations.sql

# 10. **必须**：回填存量安装 Key 的来源限制与哈希（配合第 4、6 步）
uv run python scripts/migrate_worker_install_keys.py
```

要点：

- 第 2、3、5 步含 `CREATE INDEX CONCURRENTLY`，`psql -f` 会在事务块外以
  autocommit 执行；不要手工包进 `BEGIN/COMMIT`。CONCURRENTLY 中途被打断可能残留
  永久 INVALID 索引，重跑前先按 `pg_index.indisvalid = false` 查出同名索引
  `DROP INDEX CONCURRENTLY` 再重跑（各 SQL 文件头注释已给出查询）。
- 第 10 步的 `scripts/migrate_worker_install_keys.py` 是 allowed_source 与注册回收列
  的**强制**配套回填脚本：仅加列不回填，存量 Key 缺来源限制会被新运行时拒绝。
  幂等可重跑，细节见上一节「升级旧版 Worker 安装 Key」。
- 若只跑 `init_db.py` 而不执行上面的 SQL：列会被自愈，`idx_task_logs_run_id_id`
  与 `idx_worker_install_keys_unacknowledged_recovery` 也会由 `_create_performance_indexes`
  并发补建，但 task_executions/scheduler_outbox 的租约/消费索引以及
  registration_id 唯一索引不会自动补——请照上表手工执行对应 SQL。

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
- **数据库 TLS**：`sslmode` 只接受 PostgreSQL 标准值；`verify-ca` / `verify-full`
  必须在同一 `DATABASE_URL` 中显式配置 `sslrootcert`。应用启动时会加载 CA，
  路径不存在、CA 无法解析、重复参数或互相冲突的组合都会立即失败，不能静默降级。
- **保留策略**：`task_logs` / `audit_logs` / `worker_events` 由 master 的 `log_cleanup_service` 定期清理，参见 `.env` 里 `TASK_LOG_RETENTION_DAYS` / `AUDIT_LOG_RETENTION_DAYS` / `WORKER_EVENT_RETENTION_DAYS`。

## 常见问题

**Q: `init_db.py` 报 "DATABASE_URL 缺少 password"**
A: `.env` 里 DATABASE_URL 必须完整：`postgresql://user:pass@host:port/db`。

**Q: `duplicate key value violates unique constraint "users_username_key"`**
A: 数据库里已经有同名 admin，脚本会自动跳过创建。看到这条错说明是并发执行了两次，重跑一次即可。

**Q: 想清空数据库重来**
A: `DROP DATABASE "AntCode"; CREATE DATABASE "AntCode" OWNER antcode;` 然后重跑 `init_db.py`。⚠️ 会丢所有数据。
