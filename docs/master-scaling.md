# Master 水平扩容

原本 master 8 个 loop 全部通过 `ensure_leader()` gate，follower 干等；
T6-T2 把三个天然可分片的 loop 释放出来，follower 也承担 stream ingest
负载。

## Loop 分工

| Loop | 分类 | 说明 |
|---|---|---|
| `scheduler_loop` | Leader-only | APScheduler；起两次会重复触发 cron |
| `reconcile_loop` | Leader-only | 全表 UPDATE，多实例会撞 PG 行锁 |
| `artifact_cleanup_loop` | Leader-only | 24h 单次 GC |
| `crawl_batch_status_loop` | Leader-only | 全表扫描 + UPDATE |
| `alert_check_loop` | Leader-only | 全表扫描 + 告警通知去重 |
| `lease_sweeper_loop` | 每实例 | 无 leader gate；每 1s tick，重复扫也不会写坏 |
| **`result_loop`** | **Shardable** | XREADGROUP consumer group，按 consumer 分区 |
| **`log_ingest_loop`** | **Shardable** | 同上 |
| **`scheduler_event_loop`** | **Shardable** | 同上；delivery counter 走 `XPENDING deliver_count` 而不是本地 dict |

Shardable loop 的 consumer name 是 `hostname-pid`（R2-P0-2），Redis Streams
天然按 consumer 名分区消息，多实例自动均衡。老 PEL 通过 `XAUTOCLAIM
min_idle=60s` 由任一实例回收。

## 部署方式

单实例（默认 dev）：什么都不做，唯一 master 抢到 leader lock，8 个 loop
全跑。

多实例（生产）：横向堆 master，每台跑相同的镜像/命令。leader lock 抢
不到的实例只跑 shardable loop。

`infra/docker/docker-compose.dev.yml` 里加一个 `master-2` 服务复用同一
配置即可：
```yaml
master-2:
  <<: *master
  container_name: antcode-master-2
```

## 观察分片是否生效

```
# consumer 列表 —— 每台 master 一个 hostname-pid consumer
redis-cli XINFO CONSUMERS antcode:task:result antcode:workers

# 每个 consumer 的 pending 数应大致均匀
```

## 已知在流量倾斜下的行为

如果某个 master 实例专门在处理长任务的 result（比如 rule 项目结果 payload
偏大），它 pending 会堆积。`XAUTOCLAIM min_idle=60s` 会把老消息挪给
别的 consumer 处理，天然自平衡。不需要额外配置。

Leader lock TTL 30s、每 10s verify（`services/master/src/antcode_master/
leader.py`）—— leader 挂后 standby 最多 30s 内接管，接管期间 leader-only
loop 有一小段窗口不跑（reconcile 差一个 tick，不影响正确性）。
