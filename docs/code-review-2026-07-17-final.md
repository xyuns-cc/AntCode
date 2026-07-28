# AntCode Claude 修复后最终复审报告（2026-07-20）

> 本文覆盖并替代该文件 2026-07-17 的旧结论。复审对象是当前未提交工作树：
> 300 个已跟踪文件发生变化，393 个新增文件。所有复审代理均只读，未修改业务代码。

## 1. 最终结论

当前版本**不能进入测试机系统验收、不能压测、不能发布生产**。

原因不是仅有格式或测试噪声，而是仍存在可触发的 P1 正确性、安全和分布式一致性缺陷：Gateway 任务结算缺完整 Lease generation fence、日志和重排不具备响应丢失幂等性；SSE 将 PostgreSQL `BIGSERIAL` 当作提交顺序会永久漏日志；取消、重试、Worker 删除和项目级联存在状态破坏；Direct Redis 仍有坏帧永久阻塞、错误客户端/namespace 和日志大小契约不一致等问题。

本轮遵循“先审查；有问题则写报告；无问题才上测试机”的门禁。由于已确认代码问题，**本轮未连接测试机、未重建镜像、未执行远程 E2E 或压测**。

严重度定义：

- **P0**：生产发布条件缺失，必须阻断发布。
- **P1**：高风险代码缺陷，可导致越权、数据丢失、重复执行、永久阻塞或错误成功。
- **P2**：中风险功能、恢复、容量、部署或质量门禁缺陷。
- **P3**：非阻断维护债务或协议漂移风险。

## 2. P0 生产发布阻断

### P0-01 缺少真正的生产部署画像

- `infra/k8s/` 仍只有 `.gitkeep`。
- `infra/docker/docker-compose.remote.yml:9-25` 明确使用 HTTP、`AUTH_COOKIE_SECURE=false` 和关闭 Redis ACL 的验收配置。
- `infra/docker/docker-compose.remote.gateway.yml:13-14` 明确使用明文 gRPC。
- 当前没有 HTTPS/HTTP2 ingress、Gateway mTLS Secret mount、生产网络分区、滚动升级、PDB、多副本、migration job、备份恢复和 HA 清单。

测试 Compose 不能替代生产部署证据。

### P0-02 不可信任务的容器隔离权限过高

- `infra/docker/docker-compose.remote.yml:99-104` 与 `docker-compose.dev.yml:189` 同时启用 `SYS_ADMIN`、`seccomp=unconfined`、`apparmor=unconfined`、`systempaths=unconfined`。
- `services/worker/src/antcode_worker/executor/sandbox.py:246` 使用 `--ro-bind / /` 暴露几乎整个容器文件系统，只在 `:269-293` 遮蔽少数目录；额外挂载、`/run/secrets` 或其他 Worker UID 可读配置可能被任务读取并经日志/产物带出。

当前没有 gVisor、Kata、独立 VM 或专用隔离 Worker 节点画像，不能把该模式用于不可信多租户生产任务。

## 3. P1 高风险代码缺陷

### 3.1 Gateway、Lease 与传输

#### P1-GW-01 任务投递与 ACK 未形成完整 generation fence

`StreamTasks` 只在阻塞读取前校验 Lease，Redis 返回任务后未复检即下发：

- `services/gateway/src/antcode_gateway/services/data_service.py:174-205`
- `services/gateway/src/antcode_gateway/handlers/poll.py:358`

同时 `contracts/proto/data.proto:81` 的 `AckTaskRequest` 没有 `lease_id`，ACK 只校验 Worker 身份和 receipt。旧 L1 进程可在 L2 已建立后继续收到任务并 ACK/requeue；consumer name 又只有 Worker ID，服务端无法区分代际。

#### P1-GW-02 Gateway requeue/DLQ 非原子且非幂等

`services/gateway/src/antcode_gateway/handlers/poll.py:407-455` 依次执行 `XRANGE -> XADD -> XACK`。在 `XADD` 成功、`XACK` 失败或响应丢失时，重试会生成重复消息；普通 requeue 还忽略 `XACK` 返回值。Direct 已有 Lua marker 结算，Gateway 尚未具备同等语义。

#### P1-GW-03 LogAck 响应丢失会重复入库

- Gateway 每次重试产生新 Redis message ID：`services/gateway/src/antcode_gateway/handlers/logs.py:156-158`。
- Worker 未收到 ACK 会重发：`services/worker/src/antcode_worker/transport/gateway/transport.py:875-889`。
- Master 幂等键使用 `redis_msg_id:index`：`services/master/src/antcode_master/ingester/log_ingest_message.py:28-78`。

同一业务日志重发后得到不同幂等键，会重复写 PostgreSQL 并分配新 sequence。Direct XADD 响应丢失也有同类 at-least-once 重复语义。

#### P1-GW-04 run ownership 的 Lease 校验与 claim 存在 TOCTOU

`services/gateway/src/antcode_gateway/services/run_ownership_rpc.py:140-231` 将当前 Lease 校验、TaskRun 校验和 ownership Lua 分成三个操作；Lua 本身不校验权威 Lease key。L1 通过前两步后切换到 L2，L1 仍可创建/续期 3900 秒 ownership，阻塞新代际约 65 分钟。

#### P1-GW-05 client-streaming RPC 只在建流时限流一次

`services/gateway/src/antcode_gateway/rate_limit.py:343-346` 仅在 RPC 建立时收费。`StreamStatus`、`StreamLogs`、`StreamSpiderData` 和 Artifact upload 的 iterator 未逐帧计费（`data_service.py:259,307,370`）。持有有效 Worker 凭据的客户端可用单流持续写 Redis/数据库并绕过请求桶。

### 3.2 Direct Redis 可修缺陷

#### P1-DR-01 run ownership 使用错误的 Redis client/namespace

`services/worker/src/antcode_worker/engine/engine.py:1321` 重新获取全局 `get_redis_client()`/`redis_namespace()`，没有复用 RedisTransport 中按 Worker ACL 注入的 client 和 namespace。自定义 `WORKER_REDIS_NAMESPACE` 会串线；只配 `WORKER_REDIS_URL` 时 claim 可失败；额外配置服务端 `REDIS_URL` 又会绕开 per-Worker ACL。

#### P1-DR-02 坏 task/control 帧可永久阻塞当前 generation PEL

`services/worker/src/antcode_worker/transport/redis/transport.py:352-428,950-997` 解码失败后只重置 recovery；`reclaim_generation.py:116-126` 又明确跳过当前 consumer。坏帧没有当前代际 DLQ/隔离出口，一个事件即可持续堵塞任务面或控制面。

#### P1-DR-03 Direct 日志生产端与消费端大小契约不一致

- Direct 只按 run/条数打包并直接 XADD：`transport.py:677-727`、`logs/batch.py:275-320`。
- 共享契约规定批次 8 MiB、单行 1 MiB：`packages/antcode_core/src/antcode_core/common/log_limits.py:7-8`。
- Master 在消费端才拒绝超 8 MiB：`services/master/src/antcode_master/ingester/log_ingest_integrity.py:40-48`。

Worker 会报告 XADD 成功，Master 随后将批次放入 DLQ，造成日志永久丢失。

#### P1-DR-04 Lease 迁移遗漏 active/expiring 索引

`scripts/migrate_lease_keys.py:133-151` 明确跳过旧索引，只迁移 Hash；新 LeaseStore 只读取新 `{ns}:lease:active/expiring` 索引。升级后 Lease 不会出现在 list/sweep 中，失租清理副作用不会触发。脚本还原样 RESTORE 读取时 PTTL，迁移耗时可能延长或短暂复活 Lease。

### 3.3 SSE 后端与前端

#### P1-SSE-01 `task_logs.id` 不是提交顺序，权威游标会永久漏日志

`task_logs.id` 是 `BIGSERIAL`（`domain/models/task_log.py:37`），但多 Master/多 Web 写入没有 per-run 提交串行化（`postgres_log_service.py:69-112`）。SSE 使用 `MAX(id)` 与 `id > watermark`：

- `streams/ingest_history.py:118-134`
- `streams/log_stream_gap.py:36-48`
- `streams/ingest_recovery_query.py:23-79`
- `streams/log_stream_replay.py:99-113`

事务 A 先取得 ID 100 后阻塞，事务 B 取得 101 并先提交；游标推进到 `pg:101` 后，A 晚提交的 100 会被实时判重，恢复查询也永远不会读取。这是生产级永久漏日志问题。

#### P1-SSE-02 active gap 无行数/字节预算

`log_stream_active.py:132-146` 会完整遍历 `log_stream_gap.py:36-93` 的固定快照；该路径没有历史/recovery 的 10000 行、8 MiB 上限。follower 长时间落后时，每个订阅都会独立扫描和发送整个缺口，并在此期间不消费 broker 队列，可形成放大和恢复活锁。

#### P1-FE-01 带游标恢复时重连熔断失效

`web/.../logStreamConnection.ts:87` 在有 cursor 时预设 `historyComplete=true`；服务端固定先发 `run_status`，前端在 `:104-106,243-249` 立即把重试次数清零。若随后持续收到 `recovery_unavailable`，客户端会无限换票/重连，永远达不到 5 次熔断。

#### P1-FE-02 后台标签页会形成全量历史重连风暴

`useLogMessageBuffer.ts:71-83` 在隐藏页 RAF 不执行且积压达到 maxLines 时清空并 overflow；`useLogStreamController.ts:247-252` 随即创建无 cursor 的新连接。超过 5000 行的历史可反复执行“回放 5001 行 -> overflow -> 1 秒后全量重连”，持续消耗 ticket、PostgreSQL 和 Web API。

### 3.4 取消、重试、调度与 Worker 生命周期

#### P1-FN-01 待重试取消实际无效

`services/web_api/src/antcode_web_api/routes/v1/retry.py:173` 只改状态，不删除 Redis pending，也不清 `next_retry_at`。Master 会从 PG 恢复 intent（`master/control/retry_loop.py:661`），源校验又不检查取消状态，最终仍会创建新 run。

#### P1-FN-02 run 取消可继续执行或返回虚假成功

- 未分配 run 取消只改 `status/end_time`，不更新 runtime/dispatch/Task：`routes/v1/runs.py:102`、`tasks.py:1198`；Master 仍可将其派发。
- 已分配 run 取消忽略状态机 CAS 返回：`runs.py:75`、`tasks.py:830`；完成结果抢先落终态时接口仍返回 cancelled。

#### P1-FN-03 手动重试破坏历史 run 状态

`scheduler/retry_service.py:403` 允许任何非 RUNNING run，并把旧终态 run 改回 PENDING，却保留 terminal `runtime_status/end_time`，随后又创建新 run，形成字段矛盾且返回旧 run_id。

#### P1-FN-04 自动重试可能静默丢失

- `master/ingester/result_loop.py:312` 捕获重试调度异常后仍 ACK 结果；异常若发生在 durable intent 写入前，PG/Redis 均无重试证据。
- `master/control/retry_loop.py:579,616` 连续失败 5 次后直接清除 intent，没有 DLQ 或用户可见终态。

#### P1-FN-05 Master 在活动任务加载失败后仍报告 ready

`scheduler_loop.py:651` 吞掉活动任务加载异常，`:148` 仍置 `_scheduler_ready=True`；readiness 只检查 PG/Redis（`master/readiness.py:67`）。一个坏任务可阻止后续任务加载，但容器持续 healthy。

#### P1-FN-06 删除在线 Worker 会破坏活跃执行

`worker_service.py:230,329` 没有未终态 run 保护，先将所有 `TaskRun.worker_id` 清空再删除 Worker；`task_run_service.py:180` 又要求结果 Worker 仍存在且匹配，在途结果会被永久拒收。

#### P1-FN-07 直接分发异常会残留永久 pending run

`routes/v1/workers.py:1562` 先创建 TaskRun，之后才校验 rule detail/transport。抛异常时没有补偿，只有 `DispatchResult.success=False` 分支会标记失败。

### 3.5 数据事务与不可变证据

#### P1-DB-01 Artifact cleanup 与快照创建竞态可损坏数据

`artifact_cleanup_service.py:77-99` 先删除 chunks，再二次查询删除 metadata；`RunSourceSnapshot.artifact_id` 只是无 FK 的整数（`run_source_snapshot.py:11-24`），快照在独立事务创建（`source_bundle_dispatch_service.py:114-126`）。两条 DELETE 之间插入快照可留下 metadata，但 chunks 已永久删除。

#### P1-DB-02 所谓 immutable RunSourceSnapshot 会被覆盖

`source_bundle_dispatch_service.py:78-126` 每次 dispatch 重建 bundle，并用 `update_or_create` 覆盖同一 `(run_id, project_id)`。投递失败后 Git ref 前移，重投可让同一 run 的 commit/artifact 发生变化，破坏审计证据。

#### P1-DB-03 Scheduler outbox 仍有重复副作用窗口

`scheduler_event_loop.py:317-329` 先执行非幂等业务，再写 `consumed_at`；崩溃或提交失败后重新接管会再次触发。claim/heartbeat 又使用各主机 `datetime.now()`（`outbox_service.py:83-118`），时钟偏差可造成并发接管。

#### P1-DB-04 项目级联遗漏与 TOCTOU

`relation_service.py:248-299` 没有删除 `CrawlBatch`；项目删除会产生逻辑孤儿。task IDs/活动 run 检查还在事务外（`:234-246`），可与 `scheduler_service.py:837-875` 创建 TaskRun 竞态；单任务删除在 `scheduler_service.py:391-427` 有同类窗口。

#### P1-DB-05 注册过期清理绕过正常撤销链

`registration_cleanup_service.py:42-60` 直接批量 `Worker.delete`，没有执行 `worker_service.py:247-358` 的 heartbeat、权限、runtime、项目/任务归属和 Redis ACL revoke。未 ACK Worker 的 lease、ACL 和逻辑关系可能残留。

#### P1-DB-06 Redis 故障重连会泄漏 client/pool/健康任务

`infrastructure/redis/client.py:50-134` 的 `connect()` 无互斥并直接覆盖旧 client；每次连接创建健康任务，旧 client/任务不关闭。健康任务在 `:176-200` 又可递归触发连接。短暂故障和并发调用可快速倍增 Redis 连接。

#### P1-DB-07 20260713 migration 不具备测试宣称的全回滚语义

`20260713_add_scheduler_outbox_consumption.sql:2-18` 和 `20260713_add_task_run_lease_id.sql:2-21` 先 COMMIT 加列，再独立创建 CONCURRENTLY 索引。第二阶段失败时第一阶段不可回滚，但 `migration_cases.py:208-248` / `test_20260713_migrations.py:125-135` 仍断言所有列和索引消失。真实升级会处于部分提交状态。

### 3.6 授权与数据暴露

#### P1-SEC-01 项目导出包含同项目其他用户的执行结果

`routes/v1/project.py:639` 对任务按用户过滤，但 `include_logs` 在 `:622,655` 重新按项目加载全部执行，并导出 `result_data/stdout/stderr/error_message`（`domain/schemas/task.py:195`）。同项目多用户场景下，项目所有者可读取其他用户任务结果。

## 4. P2 中风险与功能合同缺陷

### 4.1 前端 SSE/UI

- 无效 SSE 帧只报错并继续，后续 checkpoint 可越过未验证帧：`logStreamConnection.ts:140-150`。
- `log_type/level` 未做运行时类型校验，合法 JSON 对象可使 `.toUpperCase()` 或 React 渲染崩溃：`logStreamProtocol.ts:28-36`、`VirtualLogRow.tsx:72-78`。
- 虚拟列表不接收外部 auto-scroll 开关：`EnhancedLogViewer.tsx:62`、`useVirtualizedList.ts:195-208`。
- `renderedIds` 永不清理，长期开页按历史总量增长：`VirtualizedLogList.tsx:14-19`、`useVirtualizedList.ts:178-191`。
- 后端允许 sequence 0，但 buffer 对 `<=0` 不去重：`useLogMessageBuffer.ts:40-42`。
- UI 只保留 5000 行，后端可回放 10000 行，导出仍无截断提示：`ExecutionLogs.tsx:590`、`EnhancedLogViewer.tsx:74-80`。
- overflow 后通知也被 `pendingOverflowed` 丢弃：`useLogMessageBuffer.ts:71`、`useLogStreamController.ts:250`。
- 网络重试仅 5 次、总退避约 13.2 秒且无 jitter，失败后不会自动恢复：`logStreamConnection.ts:199-213`。

### 4.2 后端 SSE 与 Gateway

- run_status PG 补偿只覆盖终态，丢失 running 事件后页面可长期显示旧状态：`log_stream_access.py:52`、`log_stream_active.py:214`。
- Redis capacity 的 run/user ZSET 不设 TTL，崩溃后一次性 key 可永久残留：`stream_capacity_limiter.py:16`。
- broker 没有 shutdown，续租任务只能等待 Lease 自然过期：`run_stream_broker.py:104`、`lifespan.py:400`。
- Artifact 上传虽然先落临时文件，最终仍 `path.read_bytes()` 并在 `PostgresArtifactStore.write_blob()` 再次切片，单流可占用数倍于 100 MiB 的内存：`artifact_service.py:250-258`、`artifact_store.py:58-77`。
- mTLS 身份允许宽松后缀匹配，而非精确解析 SAN/CN：`gateway/auth.py:701-708`。

### 4.3 Direct Redis

- `RedisTransport.get_status()` 原样返回包含 ACL 密码的 `_redis_url`：`transport.py:1196-1204`。
- global control 固定优先 per-worker，可能饥饿；Direct-only 路径从不安全裁剪 global stream：`transport.py:970-983,1188-1194`。
- Worker 初始 Lease 又用本机 `time.time()` 比较 Redis 绝对过期时间：`app/lifecycle.py:185-202`。

### 4.4 功能合同

- `POST /tasks/{id}/execute` 忽略 `execution_config/environment_variables`：`routes/v1/tasks.py:937`。
- `max_instances` 模型可配置但 scheduler 未使用：`master/control/scheduler_loop.py:661`。
- `dependency_ids` 只存 JSON，没有存在性、环检测或调度依赖语义：`routes/v1/tasks.py:631`。
- trigger/execute 在 Master 建新 run 前查询 latest，可能返回上一次 run ID：`tasks.py:891`。
- YAML 导出未正确引用 `null/on/off/数字字符串` 等保留标量：`tasks.py:117`。
- 项目 `include_logs` 实际只导出 execution 元数据，不查询 TaskLog：`project.py:655`。
- Go 外部依赖使用新的空 `GOMODCACHE`，依赖交给沙箱内 `go run`，但生产默认 `--unshare-net`；未 vendor 的正常 Go 项目会稳定失败：`go_execution_policy.py:14`、`plugins/code/plugin.py:217`、`Dockerfile.worker:172`。

### 4.5 数据与迁移

- `init_db.py` 只补列，不验证 `pg_index.indisvalid`、列序、谓词、唯一性；同名 INVALID 索引会被 `IF NOT EXISTS` 跳过并误报成功：`scripts/init_db.py:417-459`。
- Outbox 发布失败和消费失败共用 attempts；发布已失败 4 次时，首次消费失败即可永久终止：`outbox_service.py:145-198,259-269`。

### 4.6 质量门禁与供应链

- 严格复杂度门禁没有实现函数 50 行、文件 300 行、嵌套 3、magic number，也不检查 TS/TSX：`scripts/complexity_analysis.py:15-25`。当前显示 341 个基线并通过，但新增代码已经违反仓库硬规则。
- Proto 生成只修 `.py`，未修 `.pyi` 相对导入；`artifact/control/data_pb2.pyi:1` 均错误使用顶层 `import common_pb2`。`mypy.ini` 的 `ignore_missing_imports` 将其降级为 Any：`scripts/generate_proto.py:66`、`mypy.ini:16`。
- 契约包没有 `py.typed`；已安装后类型信息继续退化：`packages/antcode_contracts/pyproject.toml:15`。
- `scripts/gen_proto.sh` 与 Python 生成入口行为不一致；生成桩要求 grpcio 1.76，但契约包仍声明 `>=1.60`。
- 四个后端 Dockerfile/主要 CI 的 `uv sync` 未加 `--frozen`。
- CI 只 collect E2E，镜像发布不受真实 Direct/Gateway E2E 阻断：`.github/workflows/ci.yml:132,360`。
- 发布前只扫描 amd64，多架构 manifest push 后才再次扫描；问题标签可能已可拉取：`docker-build.yml:88-109`。
- Worker Compose 缺 `stop_grace_period`，Docker 默认终止窗口短于 Worker 30/35 秒 drain：`worker/app/main.py:202`。
- 服务缺 CPU/memory/PID/ulimit、日志轮转和临时磁盘配额。
- 数据库、Redis、加密密钥主要仍经环境变量注入，缺统一 `_FILE`/Secret mount。
- 默认 `API_BASE_URL` 推导为 HTTP，远程安装器又拒绝非 loopback HTTP；生产不显式配置时安装必失败：`common/config.py:209`、`worker_installer.py:88`。

## 5. 明确保留的架构边界

### 5.1 Direct 只能用于可信单租户/测试

即使修复第 3.2 节的代码缺陷，Direct 仍有无法由静态 Redis ACL 表达的结构限制：

- 共享 Lease Set/ZSet 无法按 member 隔离，Worker 可污染其他 Worker active/expiring member。
- global stream ACL 无法限制 consumer group 名。
- `spider:*`、索引、dedup、`run:owner:*` 缺 Worker 维度。
- task stream 与 Lease key 跨 slot，settlement Lua 无法原子校验权威 Lease。
- ACL 无法限制单次 result/log 写入大小。
- runtime action 执行完成、marker 提交前崩溃，无法保证严格 exactly-once。

因此生产必须使用 Gateway；Direct 不应作为多租户或生产安全模式。

### 5.2 Gateway 当前也尚未满足生产条件

Gateway backendless Worker 的数据库/Redis 凭据隔离已经闭环，但第 3.1 节的任务 fence、结算幂等、日志幂等、ownership TOCTOU 和逐帧限流仍是生产阻断。

## 6. 已确认修复到位的部分

- Gateway Worker 最终 Compose 合并后 `DATABASE_URL`、`REDIS_URL`、`WORKER_REDIS_URL` 均为空，且不继承 JWT、加密密钥或管理员密码。
- Gateway Artifact 下载/上传已校验 principal、当前 Lease、TaskRun ownership、source snapshot、offset、size 和 SHA-256。
- Control Watch/Ack 的逐消息 Lease fence、PEL owner 和 runtime result 恢复路径已闭环。
- Direct generation consumer、当前代际长任务不自我 reclaim、task settlement marker、DLQ 幂等证据和 Redis TIME/PTTL Lease 判定已实现。
- SSE 会话失效、用户停用、角色降级和资源权限变化均周期 fail-closed 复核。
- 历史/recovery 已有限行、UTF-8 字节预算和逐行 guard checkpoint；cursor 过期显式上报。
- 日志主链顺序是 PostgreSQL append -> 按 event ID 回读 -> SSE publish -> XACK；发布失败保留 PEL。
- Artifact metadata/chunks 新写入在同一 PostgreSQL 事务，读取校验顺序、数量、大小和 hash。
- access/refresh/session expiry、管理员任务导出 secret、Node registry 凭据、YAML alias、CSV 公式、批量上限、Git 网络/磁盘/对象配额已修复。

## 7. 本地验证证据

| 门禁 | 结果 |
| --- | --- |
| 后端全量 Unit（60 秒硬超时） | **失败**：`1 failed, 1907 passed, 6 skipped`，31.41s |
| 后端失败详情 | `test_antcode_scrapy_safe_egress` 使用真实 `example.com` DNS；当前环境解析到保留地址 `198.18.0.55`，测试未隔离 DNS，属于非 hermetic 测试门禁失败 |
| 前端 Vitest | **失败**：`6 failed, 80 passed`，3 个文件失败 |
| 前端失败范围 | checkpoint 合同 4 项、history 完成帧 1 项、buffer overflow 合同 1 项 |
| Ruff lint | 通过 |
| Ruff format check | **失败**：44 个文件需要格式化 |
| mypy（仓库配置） | 通过：512 source files；但 `ignore_missing_imports` 掩盖 Proto stub 问题 |
| Proto 严格 mypy | **失败**：18 errors，其中 3 个 `common_pb2 import-not-found` |
| Proto 临时重生成 diff | 生成物内容一致；一致地保留了错误 `.pyi` 导入 |
| 复杂度门禁 | 通过：341 个审计基线；门禁覆盖不完整，不能证明硬规则通过 |
| 前端 ESLint | 0 error，1 warning：`useLogMessageBuffer.ts:84` 缺 `maxLines` dependency |
| 前端 TypeScript / production build | 通过；3322 modules transformed |
| Bandit | HIGH 0；MEDIUM 26，扫描因中危项返回非零 |
| `git diff --check` | 通过 |
| npm 在线 audit | 未执行；工具因外部依赖元数据披露策略拒绝 |

并行定向复审现有测试虽通过（Gateway 184、Direct 150、SSE backend 92、部署契约 38、复杂度/Proto 13），但这些测试没有覆盖本报告中的响应丢失、Lease 切代竞态、晚提交小 ID、坏帧 PEL、真实取消/重试、删除竞态和客户端流逐帧限流。

## 8. 测试断层

- E2E 没有真实停止/重启 Master、Worker、Gateway、Redis、PostgreSQL；所谓 crash 测试主要是手工构造 PEL。
- Spider E2E 在删除前直接调用内部服务清 Redis，绕过生产 outbox -> Master cleanup 链。
- Worker 注册 E2E 未覆盖安装 Key、凭据落盘、ACK、响应丢失恢复和重启复用身份。
- E2E 未覆盖 manual retry、pending retry cancel、execute overrides、max_instances、依赖关系、导入导出、删除在途 Worker、跨租户隔离。
- 真实 PostgreSQL migration 的失败/部分提交语义尚未获得可信验证。
- 前端实现和测试合同已经分叉，当前不能把 type-check/build 通过等同于功能正确。

## 9. 发布判定

| 判定项 | 结论 |
| --- | --- |
| 代码审查无 P1 | **否** |
| 后端/前端自动化全绿 | **否** |
| Gateway 生产传输闭环 | **否** |
| Direct 可用于生产 | **否，结构上禁止** |
| 生产部署画像完备 | **否** |
| 允许进入测试机最终验收 | **否，先修复本报告问题** |
| 允许压测 | **否** |
| 允许发布生产 | **否** |
