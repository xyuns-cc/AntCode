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
| Sentinel + TLS | `rediss+sentinel://<master_name>@host1:26379,host2:26379[/db]` | `rediss+sentinel://mymaster@10.0.0.1:26379,10.0.0.2:26379/0` |

`REDIS_MODE` env（`standalone` / `cluster` / `sentinel`）覆盖 scheme 判断，
用于 URL 已经是标准 `redis://` 但背后是集群/哨兵的反代场景。

Sentinel 场景下的 master 名可用 URL 的 userinfo 段传，也可退回
`REDIS_SENTINEL_MASTER_NAME` env。

Sentinel 控制面和 Redis master 数据面使用独立 ACL 凭据。推荐全部通过 URL
查询参数显式配置；参数值含 `@`、`&`、`/` 等保留字符时必须 percent-encode：

```env
REDIS_URL=rediss+sentinel://mymaster@s1:26379,s2:26379/0?sentinel_username=sentinel-user&sentinel_password=sentinel-pass&master_username=app-user&master_password=master-pass&ssl_ca_certs=%2Frun%2Fsecrets%2Fredis-ca.pem
```

支持的查询参数只有 `sentinel_username`、`sentinel_password`、
`master_username`、`master_password`、`ssl_ca_certs`、`ssl_certfile` 和
`ssl_keyfile`。用户名必须与对应密码同时配置；client certificate 和 key
必须同时配置。重复参数、未知参数、空值、重复 endpoint、非法端口和非数字
database 都会在启动配置校验阶段直接报错。密码在连接配置对象 repr 和 Redis
连接日志中均会脱敏。

`rediss+sentinel` 会同时为 Sentinel 探测连接和 master 数据连接启用 TLS，默认
使用系统 CA 并校验证书及主机名；私有 CA 用 `ssl_ca_certs`。客户端双向 TLS
再同时提供 `ssl_certfile` 与 `ssl_keyfile`。明文 `redis+sentinel` 携带任何 TLS
参数会被拒绝，避免配置看似生效但实际仍走明文。

旧格式 `redis+sentinel://password@master@host/db` 仍兼容，authority 中的凭据
仅用于 master；它不能与 `master_username` / `master_password` 查询参数混用。

## 集群兼容注意事项

集群模式下 EVAL / pipeline(transaction=True) 里的 key 必须落同一 slot。
项目里 Lease 主记录、撤销集和全局索引统一使用 namespace hash tag：

- `{{ns}}:lease:data:<worker_id>` Hash
- `{{ns}}:lease:revoked:<worker_id>` Set
- `{{ns}}:lease:expiring` ZSet
- `{{ns}}:lease:active` Set

例如 namespace 为 `antcode` 时，实际 key 前缀是 `{antcode}:lease:`。这些 key
全部落在同一 slot，Lease grant/revoke/sweep 可在单个 Lua 中同步更新主
记录和索引。grant/renew 的 `granted_at_ms`、`expires_at_ms`、ZSet score 与
Hash PTTL 均由 Redis `TIME` 计算，不依赖应用节点时钟。Hash 在逻辑
过期后保留 5 秒，仅用于 sweeper 读取被剔除代际；`is_current` 要求
`PTTL > 5000`，因此该留存窗不会延长 Lease 有效期。

Spider run 的数据、meta、幂等 marker、project activity/expiry index、Lease 和 ownership
统一使用 namespace hash tag。例如 namespace 为 `antcode`、run 为 `run-1`
时，实际 key 是 `{antcode}:spider:run-1:data|meta|item-ids|item-order`。
项目索引使用 `{antcode}:spider:index:<project_id>` 保存最后活动时间，另用
`{antcode}:spider:index:expiry:<project_id>` 保存每个有限 TTL run 的绝对过期
时间；永久 run 不进入 expiry index，因此同一项目可安全混用不同 retention。
这些 key 必定位于同一 slot，Direct 控制面和 Gateway 可用单个 Lua 同时校验
Lease/ownership/tombstone 并提交数据。旧的 `antcode:spider:{run-1}:...`
布局不会被自动读取或迁移。
其他未使用多 key Lua 的 stream 不要求共享 slot。

## Key 迁移

老部署里 lease key 是 `<ns>:lease:<worker_id>`，当前格式是
`{<ns>}:lease:data:<worker_id>`。两者在 Cluster 中位于不同 slot，Redis 禁止
`RENAME`。迁移脚本逐个读取源 key 的 `TYPE` / `DUMP` / `PTTL`，再用
`RESTORE` 写入目标，成功后删除源 key。

迁移必须离线执行：先停止 Master、Gateway、Web API 和所有 Worker，完成
Redis 备份，然后执行 dry-run 与 apply。`RESTORE` 和源 key 的 `DEL` 无法跨
slot 原子提交；迁移中断时必须检查输出并按目标冲突策略重跑。

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

目标 key 已存在时默认立即报错，不删除任何一侧。确认数据权威后可显式
使用 `--on-conflict skip` 保留新旧两个 key，或用 `--on-conflict replace`
以源 key 覆盖目标后删除源 key。脚本不会把“目标已存在”当作迁移成功。

Spider key 也从 `spider:data:<run_id>` / `spider:meta:<run_id>` 改为上述
hash-tag 格式。部署前必须确认旧 Spider 数据已经导出或允许清空；新旧版本
不能滚动混跑，否则会分别读写两套 key。

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
REDIS_URL=rediss+sentinel://mymaster@10.0.0.21:26379,10.0.0.22:26379,10.0.0.23:26379/0?sentinel_username=sentinel-user&sentinel_password=sentinel-pass&master_username=app-user&master_password=master-pass&ssl_ca_certs=%2Frun%2Fsecrets%2Fredis-ca.pem
REDIS_SENTINEL_MASTER_NAME=mymaster  # URL 里已带可省
```
