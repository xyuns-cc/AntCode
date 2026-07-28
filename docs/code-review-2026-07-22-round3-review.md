# AntCode 第三轮修复后复审报告（2026-07-22）

> 复审对象：当前未提交工作树，以及 `docs/code-review-2026-07-20-round2-fixes.md` 的修复声明。
>
> 复审方式：主审配合多代理，分别覆盖 Gateway/Lease、Direct Redis、任务状态机、数据库事务、SSE/日志、前端、安全、Worker 沙箱、部署和供应链。
>
> 本轮性质：只读代码复审与本地自动化验证；未修改业务代码，未连接测试机或真实 PostgreSQL/Redis。

## 1. 执行结论

当前版本仍然**不能签署生产可用，也不应开始压测**。

`docs/code-review-2026-07-20-round2-fixes.md` 中“30 项 P1 全部修复”的结论不成立。本轮确认了 3 个 P0 生产阻断和 61 个可独立跟踪的 P1 问题，可导致沙箱前置命令劫持、任务重复执行、取消穿透、旧 Lease 越权结算、日志泄密/丢失、数据悬空、内存耗尽和不完整发布。现有单元测试多数验证稳定顺序下的局部行为，没有覆盖双进程交错、响应丢失、跨存储提交、崩溃恢复、慢消费者和容量极值。

本地静态门禁和 CI 单测分片总体为绿，但完整 `tests/unit` 单命令在 60 秒硬超时内未完成，真实 Redis/PostgreSQL contracts/integration、测试机 fresh E2E、Gateway 全链路、浏览器 E2E 和负载场景均未在本轮执行。因此不能以当前绿测推导“稳定无错误”。任何代码复审也不能证明零缺陷；本报告给出的是已证实问题和当前证据边界。

## 2. P0 生产阻断

### P0-01 Worker 沙箱启动命令可被任务环境中的 `PATH` 劫持

`services/worker/src/antcode_worker/app/wiring.py:643-656` 用 `shutil.which()` 验证 `bwrap`，但仍把原始相对命令保存到 `SandboxConfig`。`services/worker/src/antcode_worker/executor/sandbox.py:586-601` 先包装相对 `bwrap`，再让 `exec_plan.env` 覆盖环境；`services/worker/src/antcode_worker/executor/process.py:319-326` 最终按该环境解析可执行文件。

当任务可控制 `PATH` 且 workspace 中存在同名程序时，伪造程序会在真实 bwrap 启动前以 Worker 容器用户身份运行。也就是说，隔离器本身尚未建立时攻击者代码已经执行，后续 `--unshare-*`、只读根目录和 rlimit 全部无效。启动时必须解析、校验并固定绝对可执行路径，且沙箱启动器解析不得受任务环境影响。

### P0-02 缺少可执行的生产部署画像

`infra/k8s/` 仍只有 `.gitkeep`。仓库没有生产 Ingress/TLS、Gateway mTLS Secret、NetworkPolicy、PDB、HA、多副本滚动升级、Migration Job、备份恢复和灾难恢复资源。现有 `infra/docker/docker-compose.remote.yml` 是远程测试画像，不能替代生产部署定义。

### P0-03 不可信任务的容器隔离边界仍不成立

`infra/docker/docker-compose.remote.yml:129-134` 和 dev 画像仍给 Worker `SYS_ADMIN`，并设置 `seccomp=unconfined`、`apparmor=unconfined`、`systempaths=unconfined`；`services/worker/src/antcode_worker/executor/sandbox.py:250-271` 仍以 `--ro-bind / /` 暴露宿主容器根目录；Rule 任务在 `services/worker/src/antcode_worker/plugins/rule/plugin.py:90-100` 允许网络。该组合不能作为不可信代码或多租户生产隔离边界。

## 3. P1 高风险代码问题

### 3.1 Gateway、Lease 与 ownership

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-GW-01 | Gateway 重连仍无条件采用新的 Lease。`services/worker/src/antcode_worker/transport/gateway/transport.py:1387-1428` 覆盖 `_lease_id`；`tests/unit/worker/test_gateway_transport_connection_lifecycle.py:85-102` 反而固化 L1 重连后采用 L2。 | 同一进程跨代继续执行，在途任务与 PG/Redis ownership 代际分裂。 |
| P1-GW-02 | 撤销只停止 transport。`transport.py:1600-1611` 不终止 Engine/子进程；真正 ownership 检查位于 `engine.py:1287-1290,1422-1443`，周期最长 600 秒。 | L1 失租后仍产生外部副作用，L2 可同时接管同一 run。 |
| P1-GW-03 | AckTask/requeue 仍是 Lease 检查与结算 Lua 分离。`services/gateway/src/antcode_gateway/services/data_service.py:220-234` 先查 Lease；`handlers/task_settle.py:44-81,138-155` 只检查 PEL consumer。 | 换代发生在两步之间且 L2 尚未 claim 时，L1 仍可删除或重投消息。 |
| P1-GW-04 | Redis fence 后再写 PG 形成反向竞态。`services/gateway/src/antcode_gateway/services/run_ownership_rpc.py:57-70`；`run_ownership_service.py:82-107` 的 bind 只按 `run_id + worker_id` 更新。 | L1 fence 后暂停，L2 fence+bind，L1 迟到 bind 可把 PG 从 L2 覆盖回 L1。 |
| P1-GW-05 | ownership claim/bind 不检查 TaskRun 终态。`engine.py:1107-1125` 可能先持久化结果后 ACK ready 失败；`run_ownership_service.py:23-38,82-107` 不读取终态；`handlers/poll.py:276-304` 可由 L2 reclaim。 | 已完成 run 可再次执行；终态幂等无法撤销第二次外部副作用。 |
| P1-GW-06 | `StreamStatus` 没有 current-Lease fence。`data_service.py:257-273` 只验 Worker/run 归属；`task_run_service.py:197-229` 对 PG 中仍绑定 L1 的迟到状态继续放行。 | 失租进程可在 L2 改绑前伪造 RUNNING 或终态。 |

### 3.2 Direct Redis 与 ACL

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-DR-01 | Direct L2 接管旧 PEL 只更新 Redis ownership，`services/worker/src/antcode_worker/engine/engine.py:1371-1392` 不改 PG；`task_run_service.py:154-172` 拒绝 L1→L2。 | 新代际执行产生的状态和日志因 PG 仍绑定 L1 而被拒绝。 |
| P1-DR-02 | ACL 允许 Worker 对自身 Lease 执行 `HSET/PEXPIRE`；`lease_service.py:589-599` 的 `is_current()` 只看 lease_id 和 PTTL，不检查 revoked set/逻辑到期。 | 已撤销 Worker 可伪造新 Lease 并恢复 ownership、结果和日志权限。 |
| P1-DR-03 | Worker ACL 可读取其他 Worker Lease，并向共享 result/log stream XADD；Master 只信消息体声明身份。相关入口为 `result_loop.py:52-57`、`task_run_service.py:174-229`、`log_ingest_integrity.py:51-64,100-138`。 | 任一 Direct Worker 可伪造其他 Worker 的终态与日志。 |
| P1-DR-04 | `packages/antcode_core/src/antcode_core/common/security/redis_acl_policy.py:112-124` 允许裸 `SET/DEL/PEXPIRE` 共享 `run:owner:*`。 | 一个 Worker 可删除或延长其他 Worker 的 run ownership，造成横向中止或阻塞。 |
| P1-DR-05 | task/control settlement 在 `redis/transport.py:500-515,656-666,1174-1201` 先查 Lease；`task_settlement.py:15-69`、`owned_stream_ack.py:7-16` 的 Lua 只验 consumer。 | Lease check 与 EVAL 之间换代时，旧进程仍可 ACK/requeue/确认 control。 |
| P1-DR-06 | `control_recovery.py:39-58` 以 `min_idle=0` 且空 generation guard 恢复；`reclaim_generation.py:93-105` 把所有非当前 consumer 当旧代际。 | 新旧代际可反复 XCLAIM，取消和配置控制出现活锁或饥饿。 |
| P1-DR-07 | `redis_acl_policy.py:100-103` 允许 Worker 为 global control stream 创建任意 consumer group；`global_stream_retention.py:14-25` 和 `stream_retention.py:29-53` 的裁剪需要顾及所有 group。 | 创建停在 `0-0` 的 group 即可钉住裁剪边界并持续耗尽 Redis。 |
| P1-DR-08 | ACL 轮换入口 `routes/v1/workers.py:1078-1113` 不锁 Worker 行；`common/security/redis_acl.py:95-124,183-191` 的 Redis SETUSER 与 PG save 没有 CAS。 | 并发轮换可能留下 Redis 密码 B、PG 密码 A；与撤销/删除竞态还可遗留孤儿 ACL 账号。 |

### 3.3 取消、派发、重试与恢复

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-FN-01 | API 基于陈旧的 `DISPATCHING + worker_id=NULL` 快照跳过 cancel control，Master 随后仍可绑定并入队。`services/web_api/src/antcode_web_api/routes/v1/runs.py:61`；`worker_dispatcher.py:828`。 | 数据库显示取消，但用户任务仍真实执行。 |
| P1-FN-02 | Worker dequeue 与 QUEUED cancel 非原子；`scheduler.py:134` 的 remove 失败被忽略，`engine.py:803,1221` 又忽略 `CANCELLED -> PREPARING` 失败。 | 已 ACK 为 CANCELLED 的任务仍可启动子进程。 |
| P1-FN-03 | `cancel_tombstones.py:14` 仅进程内保存 600 秒；`engine.py:375,1214` 在结算前单次 pop。 | 重启、延迟、ACK 失败或重投都会丢失取消 fence。 |
| P1-FN-04 | `engine.py:421,1230` 丢弃 `Engine.cancel()`/executor cancel 的 False，`engine.py:431` 也忽略 control ACK False。 | 进程未停止仍上报取消成功，控制面和真实执行状态分裂。 |
| P1-FN-05 | Crawl batch cancel 在 `batch_dispatcher_service.py:70,132` 使用一次性 active-run 快照，旧 handler 可在快照后继续创建 run。 | 已取消 batch 仍产生新任务，且不会再收到取消。 |
| P1-FN-06 | `packages/antcode_core/src/antcode_core/infrastructure/redis/streams.py:163` 的 pipeline 可透明重放整个 MULTI/EXEC；外层 `stream_dedup.py:28` 只查尾部 512 条。 | XADD 响应丢失可写入两次并触发双执行。 |
| P1-FN-07 | `scheduler_loop.py:1114` 忽略将原 run CAS 到 FAILED 的返回值，仍创建 retry run。 | 原 run 已成功时仍产生另一 run 并重复执行。 |
| P1-FN-08 | `dispatch_bind_guard.py:62` 只更新 `worker_id`，不推进查询状态谓词。 | 两个请求可重复命中 CAS，把同一 run 派发到不同 Worker。 |
| P1-FN-09 | FAILED run 可重派，但 `worker_service.py:251` 的删除活跃集合不含 FAILED。 | 绑定提交后 Worker 可被并发删除，任务进入不存在的 Worker stream。 |
| P1-FN-10 | `scheduler_loop.py:976` 的合法 busy 在约 20-25 秒内五次失败后，被 `retry_loop.py:571` 当 poison 清除。 | 配置允许重试的任务会永久丢失 retry intent。 |
| P1-FN-11 | `dispatch_ack_liveness.py:66` 只看 Worker 级活性，不校验 run Lease 代际，判死与 PEL reclaim 也无共同 fence。 | 新代际心跳可永久掩盖旧代际僵死 run，或与 reconcile 形成竞态。 |
| P1-FN-12 | `task_run_service.py:70` 附近的结果 Lease 校验、PG bind、状态更新是独立步骤。 | X→Y 改绑发生在步骤之间时，旧代际结果仍可结算。 |
| P1-FN-13 | 所有 Master 在 `services/master/src/antcode_master/__main__.py:247` 无条件执行启动恢复；`task_persistence.py:226` 把 Lease 查询错误吞成空集。 | 多副本或 Redis 短故障可把活跃 run 判死并重新执行。 |
| P1-FN-14 | Master 异常、Timeout、reconcile 判死和 Lease eviction 等路径只写失败终态，不创建 durable retry intent。 | 同一重试策略对不同失败来源表现不一致，任务无法按配置恢复。 |

### 3.4 数据事务与生命周期

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-DB-01 | standby Master 也消费 `task_trigger`；`scheduler_event_loop.py:110,323,379` 将只存在于进程内 APScheduler 的 job 建立后就标 outbox consumed；`trigger_idempotency.py:32` 尚未创建 TaskRun。 | standby 消费或 leader 在 job/run 间崩溃时，触发事件永久丢失。 |
| P1-DB-02 | Artifact cleanup 先建立语句快照再等待 key-share；`artifact_cleanup_service.py:79` 的跨表 `NOT IN` 不会因 snapshot 新增而复评；`run_source_snapshot.py:14` 又无 FK。 | `source_bundle_dispatch_service.py:155` 成功写入后仍可能留下悬空 snapshot。 |
| P1-DB-03 | Task/Project 删除先提交实体删除，后在 `scheduler_service.py:443`、`project_cascade_delete.py:56` purge 日志。 | commit→purge 崩溃窗口会永久保留孤儿日志。 |
| P1-DB-04 | batch run 用 `task_id=0`（`batch_dispatcher_service.py:255`）；项目删除仅按项目 Task ID 处理（`project_cascade_delete.py:80,110`），也未清 Redis progress/checkpoint。 | 项目删除遗漏 crawl run、日志、snapshot 和无 TTL Redis 数据。 |
| P1-DB-05 | Web API 直接派发在 `workers.py:1509-1516` 事务外校验 Task 后创建无 FK TaskRun，不取 Task 行锁。 | 与删除并发时可创建指向已删除 Task/Project 的孤儿 run。 |
| P1-DB-06 | HTTP 批日志按组独立提交（`workers.py:1939-1962`），失败后整体 503；`distributed_log_entries.py:19` 没有 event_id。 | 客户端重试会复制已成功组。 |

### 3.5 日志、SSE、安全与容量

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-LOG-01 | 主执行链 `engine.py:886-899 -> executor/process.py:539-583 -> logs/manager.py:225-229 -> logs/batch.py:342-352` 原样写日志；只有未接入该链的 `logs/streamer.py:202-217` 脱敏。`process.py:293-301` 还记录完整 argv。 | token、密码和命令参数可原样进入 Redis、PG 与前端。 |
| P1-LOG-02 | `task_log_service.py:82-88,106-126` 一次加载 10,000 行；`routes/v1/tasks.py:1193-1270` 为分页/下载多次 split、join、encode、BytesIO。单行允许 1 MiB。 | 单请求可制造约 10 GiB 级内存放大和 OOM。 |
| P1-LOG-03 | gap/history/recovery 虽改成 25 行，但在预算检查前整块物化；见 `log_stream_gap.py:91`、`ingest_history.py:163`、`ingest_recovery.py:60`。 | 合法 Unicode 日志可令单连接单查询先占约 100 MiB。 |
| P1-LOG-04 | legacy history 的 25/200 是 Redis 消息数而非字节（`ingest_history.py:216,242`），旧 LogBatch 可达 8 MiB。 | 单次规划约 200 MiB，正序 reply 理论可达约 1.6 GiB。 |
| P1-LOG-05 | recovery 每轮先 COUNT 全余量再仅回放 10,000 行或 8 MiB（`ingest_recovery.py:42`、`ingest_recovery_query.py:50`）。 | 长缺口接近 `O(N^2/k)` 扫描。 |
| P1-LOG-06 | `log_stream_active.py:142-156` 最多连续 drain 1000 条，期间无权限 checkpoint、字节或时间预算。 | 会话撤销或授权变化后，慢连接仍可继续输出大量日志。 |
| P1-LOG-07 | 前端 `ExecutionLogs.tsx:43-44` 与 `useLogMessageBuffer.ts:55-69` 只保留 5000 行，无字节预算。 | 合法大日志可让浏览器保留数 GiB 字符串并崩溃。 |
| P1-LOG-08 | 未知 log type 降级时，`logStreamMessages.ts:12` 生成的原始稳定 ID 被 `enhancedLogViewerUtils.ts:30` 丢弃，之后只按 `type:sequence` 去重。 | 未知类型与 system 同 sequence 时静默丢行。 |
| P1-LOG-09 | `log_ingest_message.py:21` 仍允许空 batch_id 并回退 Redis message ID。 | Direct 响应丢失重试重新 XADD 后无法幂等，日志重复入库。 |
| P1-LOG-10 | HTTP 日志入口仍无封闭类型、Lease fence 和幂等 event ID。 | 旧代际可上报任意类型；超时重试和部分提交造成重复。 |
| P1-LOG-11 | `log_ingest_message.py:190` 对极值 Proto timestamp 抛异常；`log_ingest_loop.py:225` 只让 `InvalidLogBatchError` 进 DLQ。 | 毒消息永久留在 PEL，反复阻塞消费。 |
| P1-LOG-12 | `log_ingest_loop.py:157` 只要 `>` 有新消息就返回，不处理 pending/XAUTOCLAIM。 | 持续新流量会让旧 PEL 无限饥饿。 |
| P1-LOG-13 | Gateway/Direct 对全局 ingest stream XADD 无高水位或生产端背压。 | Master 停机或积压时 stream 可无限增长直至 Redis OOM。 |
| P1-LOG-14 | TaskStatus 只限单帧 1 MiB；`task_run_service.py:92-112,284-291` 会把同一 RUNNING 帧的新 key 持续 merge 进 `result_data`。 | 有效 Worker 可按默认约 100 fps 无上界膨胀 PG JSON。 |
| P1-LOG-15 | `artifact_quota.py:8-16,29-31,96-106` 是进程内 LRU；`artifact_service.py:112-114,177-208` 上传成功后才记账。 | 重启、多副本和 LRU 驱逐都能绕过每 run Artifact 总配额。 |
| P1-LOG-16 | 项目导出的 8 MiB 预算只覆盖 task_logs；`project.py:624-656` 加载 tasks 和 200 个完整 TaskRun，`response.py:233-244` 携带无界 result_data 后整体序列化。 | 配合 P1-LOG-14，单次导出可达数百 MiB并产生多份副本。 |

### 3.6 前端功能正确性

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-FE-01 | `enhancedLogViewerUtils.ts:50-55` 在搜索文本为空时仍对每条完整内容 `toLowerCase()`；`EnhancedLogViewer.tsx:42` 每批消息重跑。 | 大日志/高吞吐下持续产生 O(总缓冲字节) CPU 与临时内存压力。 |

### 3.7 部署、迁移与供应链

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-DEP-01 | `scripts/init_db.py:116-133` 的旧库自愈漏加 ORM 必需列 `api_key_previous_expires_at`；模型位于 `domain/models/worker.py:97-107`。 | 初始化可成功退出，运行时再报 `UndefinedColumn`。 |
| P1-DEP-02 | `docs/database-setup.md:90-105` 升级清单遗漏 `20260710_secure_worker_credentials.sql` 和 `scripts/migrate_worker_credentials.py`。 | 旧 Worker 凭据不回填，升级后认证失效。 |
| P1-DEP-03 | `.github/workflows/ci.yml:238-242` 明示 Gateway TLS/mTLS 未覆盖；现有 smoke 只验注册、readiness、heartbeat。 | 任务、ACK、日志、结果、Artifact、TLS/mTLS 和切代均无真实 E2E 证明。 |
| P1-DEP-04 | `.github/workflows/docker-build.yml:174-193` 先创建正式标签，之后才安装 Cosign 并签名。 | 签名失败会留下可拉取的未签名正式镜像。 |
| P1-DEP-05 | 五服务 matrix 独立发布且 `fail-fast:false`（`docker-build.yml:45-63`）。 | 部分服务失败时其他 semver 标签已生效，版本发布非原子。 |
| P1-DEP-06 | 最终多架构镜像是 `docker-build.yml:144-158` 的第三次构建；`:163-170` 未按最终 child digest 分平台扫描。 | 无法证明最终 arm64 子镜像与扫描对象一致。 |

### 3.8 Gateway 认证与控制面

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-SEC-01 | Gateway `server.py:230-242` 只加载 CA，无 CRL/OCSP/serial denylist；纯 mTLS 在 `auth.py:312-316,681-721` 不查 Worker DB。`worker_service.py:230-246,399-406` 删除 Worker 只撤 Direct ACL。 | 被删除 Worker 的旧证书仍可绕过 Register 获取/续租 Gateway Lease。 |
| P1-SEC-02 | `services/gateway/src/antcode_gateway/config.py:60-64,169-171` 读取 `GRPC_HOST`，但 `server.py:172-191` 的 secure/insecure 监听均硬编码 `[::]`。 | 配置为 loopback 仍暴露到全网卡；与关闭认证/允许明文组合时直接扩大控制面暴露面。 |
| P1-SEC-03 | `data_service.py:167-183` 信任 int32 `prefetch`；`handlers/poll.py:208-221` pending 和 live 各取 N，没有服务端上限。 | 持凭据客户端可请求极大 N，放大 Gateway 内存和 PEL，单轮还可达到 2N。 |
| P1-SEC-04 | `contracts/proto/control.proto:110-127` 的 CancelTask/UpdateConfig 无 lease_id；`control_service.py:445-508` 只绑定 Worker 主体后 XADD，未查 current Lease，也未设置 stream maxlen/TTL。 | 旧 L1 长期凭据可向 L2 注入取消/配置，并持续灌满无界 control stream。 |

## 4. P2 中风险、可维护性与测试缺口

### 4.1 分布式正确性与数据

- Gateway reconnect freshness 使用本机 `time.time()`；poll 的 pending 与 live 各自取 `max_tasks`，单次可返回 `2 * max_tasks`。
- Lease 迁移 `scripts/migrate_lease_keys.py:203-216` 重建索引时不比较 Redis TIME、`expires_at_ms` 或 PTTL，会把 retention 窗口内逻辑过期记录重新放入 active/expiring。
- Direct TaskStatus 使用 `XADD *`，响应丢失会生成重复事件。
- `20260713`、`20260720` migration 和 `scripts/init_db.py` 只检查同名列的基础存在/类型，未完整检查 VARCHAR 长度、nullable、default 和 constraint。
- `scheduler_outbox` 无 retention；artifact cleanup 无 LIMIT，积压时形成大事务、长锁和 WAL 峰值。
- outbox claim 使用数据库时钟，但 enqueue、发布筛选、退避仍混用进程时间。

### 4.2 SSE 与前端

- gap 的 4 MiB 预算先发送后累计，只计算 content 而非 JSON/SSE 实际帧；recovery 分页异常仍可能裸断流。
- history 在 `stream_cursor` 后、结束帧前断线，前端恢复后可能永久停在 `loading`。
- 前端连续网络故障约 31-34 秒、五次重连后永久停止自动恢复；无 `online`/visibility 恢复。
- `useAuth.ts:67-81` 在 refresh 成功、权限接口临时失败时可把认证状态卡在登录页；SSE `session_revoked` 不同步清理全局认证状态。
- 5000 行滑动窗口长度恒定后，虚拟列表只按 `itemsLength` 判断新日志，自动滚动会失效；错误作为普通日志写入，重连清历史或类型过滤时会消失。
- 外置 `VITE_API_BASE_URL` 与前端 Dockerfile 固定 `connect-src 'self'` 冲突；独立 API 域名会被 CSP 阻断。
- 日志页不比较 URL 中 task 与 run 的真实归属；手动刷新内部吞错后仍提示成功。
- 缓冲使用头部 `splice`，突发流量接近 O(n²)；暂停期间会丢掉错误、权限撤销等 notice。

### 4.3 安全、部署与质量门禁

- Git URL 允许 userinfo，`repository_service.py:45-53,91-100` 明文存储/回显；scan 异常还可能包含带凭据 argv。应由加密 GitCredential 承载凭据。
- mTLS 文档 `docs/mtls-deployment.md:98-109` 使用 Worker 不支持的嵌套 YAML，实际配置是 `config.py:323-341` 的平铺字段。
- 独立 security-scan workflow 不直接阻断发布；Trivy 全部 `ignore-unfixed:true`；Linux pip-audit 不覆盖 Windows marker 依赖。
- `migrations/models/README.md` 仍声称目录不带迁移文件，与当前实际内容不符。
- 复杂度门禁虽然通过，baseline 仍有 886 项历史债务；Python 的 50 statements 不能等价于 50 lines，TypeScript 仍无函数级复杂度/长度/嵌套/参数/魔法数字硬门禁。
- mypy 仍允许较宽松的 missing imports/untyped 边界；协议实现和版本号仍有重复/漂移。
- loadtest CI 实际为 21 passed、9 deselected，没有运行真实负载场景；Gateway 全链路、真实浏览器、Redis Cluster/ACL 和故障注入测试仍缺失。

## 5. 已确认真实落地的修复

以下修复可继续保留，不应回退：

- Integration stale import 已修复，collect-only 为 157 collected / 0 error。
- 四个 CI 单测分片均在 60 秒内完成；fresh Compose E2E 已在启动全栈前调用 `scripts.init_db`。
- `20260720` migration 已加入升级清单；pip-audit 导出覆盖 141 个 workspace requirements。
- ownership/Lease Redis Cluster 同槽、死 holder takeover、task settlement marker、runtime-control marker 已存在。
- Lease 迁移可从 target SCAN 恢复索引；outbox failure release 不再清除他人 claim。
- snapshot first-write-wins 已落实；SSE active gap 已改 keyset 分页。
- Gateway/Master 对非空 batch_id 的哈希校验、单帧状态上限和基础 Artifact 配额代码已存在，但不能覆盖本报告列出的空 ID、累计状态和分布式配额问题。
- 前端 lint、type-check、Vitest 和 production build 均通过。

## 6. 本轮验证结果

| 检查 | 实际结果 | 判定 |
|---|---|---|
| 完整后端 Unit，60 秒硬超时 | 60.01 秒退出 142，约完成 71% | 失败；不满足仓库硬超时 |
| Core CI 分片 | 676 passed / 11.87s | 通过 |
| Web API CI 分片 | 369 passed / 5.26s | 通过 |
| Gateway + Master CI 分片 | 321 passed / 42.45s | 通过 |
| Worker + Scripts CI 分片 | 650 passed / 6 skipped / 4.56s | 通过 |
| Boundary | 15 passed | 通过 |
| Integration collect-only | 157 collected / 0 error | 仅收集通过，未执行真实集成 |
| Loadtest CI 命令 | 21 passed / 9 deselected | 未执行真实负载场景 |
| Ruff check / format | 通过 | 通过 |
| mypy | 537 source files / 0 errors | 通过，但配置仍较宽松 |
| 复杂度 | 通过，baseline 886 | 只证明无新增/恶化，不证明满足硬规则 |
| 前端 | 19 files / 96 tests；lint/type-check/build 通过 | 通过 |
| `git diff --check` | 通过 | 通过 |
| 测试机、真实 Redis/PostgreSQL、远程 fresh E2E | 本轮未执行 | 无验收证据 |

## 7. 验收判定

1. 当前版本：**拒绝生产发布**。
2. 当前版本：**拒绝压测**。正确性、安全边界和发布链仍有已知阻断，压测结果不具备上线判定价值。
3. `round2-fixes` 的“P1 全部修复”应撤销；应以本报告中的 P0/P1 作为下一轮修复与回归基线。
4. 在 P0/P1 全部闭环、加入相应竞态/崩溃/容量回归测试、完整 unit 命令满足 60 秒、测试机 fresh 环境通过真实中间件全链路 E2E 后，才具备重新评估压测和生产发布的前提。
