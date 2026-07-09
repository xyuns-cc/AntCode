# Redis 部署形态：单机 / 集群 / 哨兵

AntCode 全部 Redis 客户端通过 `antcode_core.infrastructure.redis.factory
.create_async_redis_client` / `create_sync_redis_client` 装配，运维只需要
改 `REDIS_URL` 的 scheme 或 `REDIS_MODE` env 就能在三种部署形态之间切换，
代码零改动。

## URL scheme 约定

| 模式 | scheme 前缀 | 举例 |
|---|---|---|
| standalone（默认） | `redis://` / `rediss://` | `redis://:pwd@10.0.0.1:6379/0` |
| Redis Cluster | `redis+cluster://` / `rediss+cluster://` | `redis+cluster://10.0.0.1:7000` |
| Sentinel | `redis+sentinel://<master_name>@host1:26379,host2:26379[/db]` | `redis+sentinel://mymaster@10.0.0.1:26379,10.0.0.2:26379` |

`REDIS_MODE` env（`standalone` / `cluster` / `sentinel`）覆盖 scheme 判断，
用于 URL 已经是标准 `redis://` 但背后是集群/哨兵的反代场景。

Sentinel 场景下的 master 名可用 URL 的 userinfo 段传，也可退回
`REDIS_SENTINEL_MASTER_NAME` env。

## 集群兼容注意事项

集群模式下 EVAL / pipeline(transaction=True) 里的 key 必须落同一 slot。
项目里唯一违规点已在 T6-T1c 修好：

- `lease_service` 的 Lua 脚本原来一次改 3 个跨 slot key。现在只操作
  `{ns}:{{worker_id}}:lease:data` 单 key（worker_id 用 hash tag 语法
  `{...}` 包起来），全局索引（`{ns}:lease:expiring` ZSet /
  `{ns}:lease:active` Set）由 Python 侧 `pipeline(transaction=False)`
  追加。索引更新失败也不影响 grant，因为 lease key 有 EXPIRE 兜底。

`spider:data:{run_id}` / `log:stream:{run_id}` / `task:ready:{worker_id}`
这类命名里的 `{run_id}` / `{worker_id}` 都是 Redis hash tag 语法本身，
天然按业务实体归位。

## Key 迁移

老部署里 lease key 是 `{ns}:lease:{worker_id}`，新格式是
`{ns}:{{worker_id}}:lease:data`。滚动升级前必须跑一遍迁移：

```bash
# dry-run（默认）
uv run python scripts/migrate_lease_keys.py \
  --url "redis+cluster://10.0.0.1:7000" \
  --namespace antcode

# 确认无误后 apply
uv run python scripts/migrate_lease_keys.py \
  --url "redis+cluster://10.0.0.1:7000" \
  --namespace antcode --apply
```

单机部署可以选择不跑，短期内老 key 因 EXPIRE 自然过期即可清理干净；
集群必须跑，否则新版代码写新命名、老命名残留数据永远读不到。

## 部署示例

单机（默认 docker-compose.dev.yml）：
```env
REDIS_URL=redis://:redispass@redis:6379/0
# REDIS_MODE 空 → 从 scheme 推断为 standalone
```

3 节点集群（生产）：
```env
REDIS_URL=redis+cluster://10.0.0.11:7000
# 集群里任一节点 URL 都可以，客户端会通过 CLUSTER SLOTS 发现全拓扑
```

哨兵 + 主备：
```env
REDIS_URL=redis+sentinel://mymaster@10.0.0.21:26379,10.0.0.22:26379,10.0.0.23:26379
REDIS_SENTINEL_MASTER_NAME=mymaster  # URL 里已带可省
```
