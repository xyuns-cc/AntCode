# migrations/models

首次发版（v1.0.0）不带任何迁移文件——**model 定义是唯一真源**。

## 全新部署

```bash
uv run python scripts/init_db.py
```

会用 `Tortoise.generate_schemas(safe=True)` 从 model 直接建表，然后补建
几个性能关键的索引，并创建默认管理员。所需环境变量与建表清单分别见
`scripts/init_db_environment.py` 与 `scripts/init_db.py` 的 `REQUIRED_TABLES`。

## 分工：init_db 自动做什么，这里的 SQL 人工做什么

**没有任何自动执行者会跑本目录的 `.sql`**——这是设计，不是缺陷。全仓唯一的
apply 者是 `tests/integration/postgres/migration_support.py`（测试用）。两边分工：

| 变更类型 | 谁负责 | 存量库要人工跑吗 |
|---|---|---|
| 新增表 | `Tortoise.generate_schemas(safe=True)` | 否 |
| 新增列 | `init_db_current_schema.py` 的 `ADD COLUMN IF NOT EXISTS` | 否 |
| 新增索引 | `init_db_indexes.py` + model `Meta.indexes` | 否 |
| 外键 / 列类型加宽 / 去重索引 | `init_db_schema_upgrades.py::align_database_integrity` | 否 |
| 凭据与敏感字段迁移 | `migrate_worker_credentials.py` / `encrypt_sensitive_data.py`（有 `antcode_data_migrations` 账本） | 否 |
| **删列** | 只有本目录的 SQL | **是** |
| **数据回填 / 脏数据修复** | 只有本目录的 SQL | **是** |

`generate_schemas(safe=True)` 只补缺失的表，`ADD COLUMN IF NOT EXISTS` 只补缺失的列
——两者都**不会**删列，也**不会**改数据。删列不可回滚、数据回填需要按业务语义判断，
把它们放进每次部署都跑的 `init_db` 才是错的。所以它们留在这里，由发布清单点名执行。

## 升级存量库到当前 HEAD：人工执行序列

先按正常流程跑完 `scripts.init_db`（`deploy-production.sh` 的 `run --rm --no-deps
migration`），**在 web-api / master / gateway 还没起来的同一个停机窗口内**，按下表顺序执行。
`ON_ERROR_STOP=1` 不能省：每个文件里的守卫都靠 `RAISE EXCEPTION` 中止，省掉它会把
"必须人工核对"降级成一条被忽略的报错。

```bash
# 逐个执行；文件从仓库工作树读，psql 在 postgres 容器里
docker compose -f infra/docker/docker-compose.prod.yml exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 \
    < migrations/models/<文件名>
```

| # | 文件 | 前置条件 | 必需性 | 可回滚 |
|---|---|---|---|---|
| 1 | `20260817_repair_project_source_include_paths.sql` | `project_sources` 已存在 | **必需**：不跑则 UI 建的 Git/文件项目打包必失败（`FileNotFoundError`） | 否（原地改写，只能靠备份） |
| 2 | `20260818_backfill_project_bound_worker.sql` | `workers` 行完整；须在 3～5 之前 | **必需**：不跑则多 Worker 集群会把任务派到没有该运行时的节点 | 否（原地改写） |
| 3 | `20260711_remove_project_source_mirrors.sql` | 1～2 已完成 | 可选清理（`entry_point` 已被 init_db 放开 NOT NULL，留着不影响运行） | **否，删列不可逆** |
| 4 | `20260819_drop_project_runtime_worker_id.sql` | 必须在 2 之后（语义已由 `bound_worker_id` 承担） | 可选清理 | **否，删列不可逆** |
| 5 | `20260820_drop_task_run_resource_usage.sql` | 无 | 可选清理 | **否，删列不可逆** |

1～2 是功能性修复，**不跑就是线上故障**；3～5 只是摘掉恒 NULL 的死列，可以推迟到
下一个窗口，但一旦执行就回不去。全部五个都幂等，重复执行是 no-op。

空库不受影响：`init_db` 直接落在"迁移后"的形状上，五个文件跑下来全是 `RAISE NOTICE`
跳过。

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
