# AntCode 第七轮修复后复审报告（非 K8s，2026-07-27）

> 审查对象：`5380f25` 与当前未提交工作树。
>
> 审查开始时工作树：275 个 modified、1 个 deleted、429 个 untracked，共 705 个状态项（不含本报告文件）。
>
> 本轮性质：多代理并行只读代码审查、本地静态检查与定向自动化验证；未修改业务代码。

## 1. 范围与方法

项目已明确不使用 Kubernetes。本轮完全排除 `infra/k8s/`、Kustomize、NetworkPolicy、K8s Secret、探针和存储，不把这些内容计入发布判定。非 K8s 部署以 Docker/Compose、反向代理、TLS/mTLS、中间件、备份恢复和 Worker 隔离为验收对象。

审查覆盖：

- 干净提交可重建性、CI、依赖与供应链；
- Web API、Master、Gateway、Worker、Direct Redis 两条传输路径；
- Lease/ownership、状态机、消息 ACK、跨存储事务和崩溃恢复；
- Worker 沙箱、凭据、认证、授权、输入边界和 SSRF；
- PostgreSQL 日志、Redis ingest、SSE 恢复、前端消费与认证联动；
- 前端路由、列表、监控、轮询、导出及生产构建；
- 复杂度硬规则、模块边界、死代码和测试可信度。

严重级别定义：P0 为发布/压测阻断；P1 为可能造成重复执行、越权、数据错误、关键功能不可用或显著资源耗尽；P2 为次要正确性、安全加固、运维和可维护性问题。

### 1.1 并行审查分工

| 审查流 | 主要覆盖 |
|---|---|
| 发布闭包/清理 | 干净 HEAD、删除链、outbox、日志/Artifact/Crawl 清理、审计与保留 |
| Worker runtime | poll/ACK、取消、结算、停机、Lease 撤销、Direct transport、runtime GC |
| Gateway/Lease | ownership fence、代际 bind、控制流、prefetch、配额、mTLS |
| Security/Authz | API 鉴权、Worker HMAC/API Key、Redis ACL、SSRF、沙箱、凭据、依赖 |
| SSE/日志 | PG cursor、gap/replay、Last-Event-ID、sequence、帧预算、前端缓冲与去重 |
| 前端认证 | 多标签 session、refresh rotation、撤权作用域、密码变更、半认证状态 |
| 前端功能 | Dashboard、Monitor、Task、Project、Repository、Crawl、Alert、分页与异步竞态 |
| 复杂度/维护性 | Python/TypeScript 硬规则、baseline 可信度、大文件、重复服务、死代码 |

主审查对代理结论做了源码复核、重复项合并和严重级别校正。每条问题要求至少满足以下一种证据：可执行复现、确定性的控制流/路由匹配、跨事务交错证明、干净快照失败或静态工具输出。

### 1.2 既有文档交叉核对

本报告以 `docs/code-review-2026-07-10.md`、仓库根 `审查报告-2026-07-10.md`、`docs/最终审查报告-2026-07-10.md` 为初始输入，并继续核对 `docs/code-review-2026-07-20-fixes.md`、后续 round2-round6 review/fixes、SSE 专项报告和 `docs/round6-remaining-risks.md`。既有文档中的“已修复”只作为待验证声明，不作为关闭证据；源码、干净提交和可执行结果优先。

本轮还主动校正两类历史结论：K8s 问题因产品决策移出范围而不是“代码修复”；WorkerProject 旧表相关文件属于未注册死代码，不应继续描述为当前生产级联删除孤儿，但其残留仍属于发布集合和维护性问题。

## 2. 执行结论

当前版本仍然**不能生产发布，也不应开始压测**。

本轮确认 3 个独立 P0、37 个 P1 问题组和 10 个 P2 问题组；问题组中的表格与清单还包含多个独立子问题。

三个 P0 为：

1. 发布提交闭包不完整，干净 `HEAD` 无法启动三个后端服务，前端无法构建，CI 复杂度门禁也确定失败。
2. 新增的非 K8s 生产 Compose 不可按其声明的 mTLS 配置启动 Gateway，Worker 身份、初始化、健康检查、迁移、前端和备份合同也未闭环。
3. Worker 结果或 ACK 重试耗尽后没有可达的耐久恢复路径，重投会永久卡住 PEL，其他 Worker 接管又可能重复执行外部副作用。

此外仍存在 Lease 换代竞态、终态重复执行、取消失败后 ACK、HTTP Worker 上报缺 fencing、SSE 无限重扫、前端会话撤销错误、Rule 网络隔离失效、凭据暴露和复杂度门禁假绿等 P1。局部测试通过不能抵消这些确定性缺陷。

## 3. P0 发布阻断

### P0-01 发布提交闭包不完整

当前运行代码依赖大量未跟踪文件。用 `git archive HEAD` 创建干净快照实测：

| 对象 | 干净 HEAD 结果 |
|---|---|
| Web API import | 通过 |
| Master import | 缺 `logs/log_sequence_allocator.py` |
| Gateway import | 缺 `antcode_contracts/artifact_pb2_grpc.py` |
| Worker import | 缺 `common/log_limits.py` |
| 前端 type-check/build | 8 个缺失模块或导出错误，build 失败 |
| 前端测试命令 | `HEAD` 没有 `test:ci` 脚本 |
| 复杂度 CI | `ValueError: Complexity baseline scope does not match the checker` |

`scripts/complexity_baseline.json` 已提交新版范围，但相匹配的 `scripts/complexity_analysis.py` 仍只在脏工作树；CI `.github/workflows/ci.yml:43-44` 因此必然失败。当前工作树中的 `logStream*`、`taskStatus`、`enhancedLogViewerTypes` 和生成的 Proto/Core 模块也未进入发布提交。

结论：当前目录能运行不代表发布对象可重建。必须以干净提交、干净依赖安装和 fresh image build 的结果作为交付证据。

### P0-02 非 K8s 生产 Compose 不可用

`infra/docker/docker-compose.prod.yml` 存在以下相互独立的问题：

- `:143-146` 设置 `GATEWAY_TLS_*`，Gateway 实际只读取 `GRPC_TLS_*`（`gateway/config.py:107-110`）。实测同等环境下 `tls_enabled=False`、`mtls_enabled=False`；`server.py:184-190` 在鉴权开启且 insecure=false 时拒绝绑定明文端口，Gateway 启动失败。
- Worker `:183-195` 只设置 `WORKER_GATEWAY_TLS=true` 和挂载目录，没有设置代码要求的 `WORKER_CA_CERT`、`WORKER_CLIENT_CERT`、`WORKER_CLIENT_KEY`（`worker/config.py:231-241`），也没有完整首次注册/身份 bootstrap 和 `WORKER_GATEWAY_BACKENDLESS=true`。
- 共享 `env_file`/anchor 把 DB、Redis、JWT、加密 key、root credentials 等注入 Gateway/Worker；这扩大了凭据泄露面，且违反服务最小权限。
- 没有 frontend service，也没有 migration/init service；镜像只支持 tag，与文档声称的 digest 部署不一致。
- Gateway healthcheck 调用镜像中不存在的 `grpc_health_probe`；Redis healthcheck 使用未注入容器环境的 `REDIS_PASSWORD`。
- backup `:282-293` 没有 `set -e`，`pg_dump` 失败后仍打印成功；仅保存在本地 volume，远端上传仍是 TODO，没有恢复演练合同。
- `Makefile:152-160` 在无默认 Compose 文件的目录执行裸 `docker compose`。

这不是“待补站点参数”的问题，而是当前生产路径在默认安全开关下确定不能启动和恢复。

### P0-03 Worker 结算失败后的重投永久卡住

`worker/engine.py:1123-1147` 在结果或 ACK 重试耗尽后保留本地状态却释放 ownership；同 Worker 收到重投时在 `:392-397` 命中 `add_if_new` 后直接跳过，既不重新结算，也不 ACK/defer，PEL 永久卡住。其他 Worker 接管则可重新执行业务并重复已发生的外部副作用。现有测试只断言“失败后保留状态”，未覆盖后续重投。

## 4. P1 分布式正确性与生命周期

### P1-DIST-01 终态检查与 Lease bind 存在 TOCTOU

`run_ownership_service.py:134-160` 先单独查询 TaskRun 是否终态，再执行 UPDATE；UPDATE 仅带 `run_id/worker_id/lease_gen`，没有排除终态。两步之间若旧 Worker 提交终态，新代际仍能 bind 并重复执行已完成 run。现有测试只覆盖顺序调用，没有事务交错。

### P1-DIST-02 Lease sequence 升级不兼容

`lease_service.py:173-186,306-310` 的新代际从 Redis INCR 的 `1,2,3...` 开始；`run_ownership_rpc.py:224-233` 对旧 Lease 回退使用约 `1.7e12` 的毫秒时间戳。PG CAS 只接受 `stored_gen <= new_gen`。迁移 `20260722_add_task_run_lease_gen.sql` 仅加列/索引，没有转换或重置旧值，存量 timestamp 行在升级后会长期拒绝新 sequence。

### P1-DIST-03 Direct 结果绑定可反向覆盖新 Lease

`task_run_service.py:95-105,231-268` 在事务外验证当前 Lease；随后 `:174-193` 仅按 `(run_id, worker_id)` 无条件写 `lease_id`，不写 `lease_gen`。L1 验证后暂停、L2 换代写 PG、L1 恢复后即可把绑定改回 L1，破坏代际单调性。

### P1-DIST-04 同 run 的多 receipt 无法稳定结算

同一 `run_id` 的不同 receipt 在 `engine.py:392-397` 被本地去重直接丢置，既不 ACK 也不 defer；Direct claim 又只检查 Redis ownership、不检查 PG 终态，断线重连或换代后该 receipt 可再次执行。

### P1-DIST-05 Direct Lease 撤销不能及时停机

Redis transport `transport/redis/transport.py:816-846` 收到 revoked 只返回 `False`，没有 Gateway `set_lease_revoked_callback` 对应机制。满载轮询在 `engine.py:316-319` 不触碰 generation guard，旧进程可能等待约 60 秒的 ownership renew 才停止；transport/readiness 还可保持 ONLINE/200，形成双执行窗口和假健康状态。

### P1-DIST-06 cancel/kill 失败仍被 ACK

`engine.py:1268-1289` 在 `executor.cancel=False` 时把状态留在 CANCELLING 并返回 False；调用方 `:448-456` 把所有 False 解释为“已终态”，`:445-446` 仍 ACK 控制消息。重复 cancel 进入 CANCELLING 后直接返回成功，不再 kill，子进程可继续运行。

### P1-DIST-07 Worker 关停不能保证清理完成

`engine.py:297-305` cancel 工作协程后不 await；协程的 kill、终态上报和 ACK 清理可与 transport/执行器销毁竞态。`lifecycle.py:252-266` 把所有清理步骤放在单个 try 中，任一步失败会跳过后续注销、心跳、执行器、runtime、可观测性和 transport 清理并吞错。`app/main.py:200-209` 的总预算只有 `grace+5`，而 Engine 内部可能消耗两份 grace，SIGTERM/SIGKILL 阶段也可能被外层超时中断。

### P1-DIST-08 Redis ACL 轮换不是跨存储原子操作

`redis_acl.py:128-183` 先在 Redis `SETUSER/SAVE`，再在 PG 事务内保存字段。若 `_save_acl_fields` 成功、事务 `__aexit__` 提交失败，异常发生在内部补偿 try/catch 之外，Redis 与 PG 凭据会分裂。

### P1-DIST-09 Artifact quota 不是分布式硬配额

`gateway/services/artifact_quota.py:138-203` 先 HGETALL、再进程内 reserve、最后 HINCRBY。多个 Gateway 可同时读相同旧值并各自放行，超过单 run 配额；Redis 失败还会静默降级为进程内账本，重启、LRU 驱逐和多副本均可重置/放大限制。

### P1-DIST-10 QUEUED ownership 过期与 runtime 清理异常可丢结果

ownership TTL 固定约 3900 秒，但续租集合排除 QUEUED；队列积压超过 65 分钟后原 Worker 与接管 Worker 可执行同一 run。`engine.py:976` 的 `runtime_manager.release()` 位于 finally 且未隔离；其异常会替换已经生成的 ExecResult，使结果不上报、不 ACK、ownership 不释放。

### P1-DIST-11 删除、清理与远端资源生命周期未闭环

| 路径 | 未关闭问题 |
|---|---|
| 项目删除 | 只删 CrawlBatch/run 数据，遗漏无 TTL 的 progress/checkpoint/workers key 和项目队列（`project_cascade_delete.py:210`、`redis_progress.py:102`） |
| 单任务删除 | 事务提交后才清日志，失败后任务已 404 且没有 outbox 恢复入口（`scheduler_service.py:410-443`） |
| Artifact cleanup | 单条 statement snapshot 判断无引用后锁行；并发创建的 snapshot 引用不可见，且 artifact_id 无真实 FK（`artifact_cleanup_service.py:79`） |
| 日志保留 | 每日扫描为所有流重设完整 7 天 TTL，无新写入的流也永不过期；循环一次异常后 task 永久退出且 `_running` 仍为 True（`log_cleanup_service.py:76,165-180`） |
| Outbox | 必需清理副作用五次失败后写 `consumed_at` 永久放弃；未知/缺字段事件静默 return 后仍标成功（`outbox_service.py:197-207`、`scheduler_event_loop.py:364-384`） |
| 用户删除 | 事务外检查项目/任务，事务内不锁 User 复查；并发创建可留下 owner 孤儿，GitRepository.owner_user_id 又无 FK（`user_service.py:407-526`） |
| Repository 删除 | 删除侧锁 repository，ProjectSource 绑定侧普通查询，无兼容锁，可提交悬空来源（`repository_service.py:63`、`project_source_service.py:164`） |
| 远端 runtime | DB 事务内创建远端环境却无失败补偿；项目/管理员删除也不检查引用或清理私有环境（`project_service.py:177`、`runtimes.py:163`） |
| Runtime GC | 检查使用计数后直接 rmtree，与 prepare 使用不同锁，任务可在检查后开始使用再被删除（`runtime/gc.py:398`、`runtime/manager.py:160`） |
| 自动重试 | 约 20 秒的 PG/Redis 异常会耗尽五次计数并清除 durable intent，把基础设施故障当 poison（`retry_loop.py:594-599`） |

## 5. P1 安全与输入边界

### P1-SEC-01 HTTP Worker 上报缺 Lease fencing 且日志类型未校验

`workers_report.py:40-63` 的日志、批量日志、心跳和状态 schema 都没有 `lease_id`；入口仅按 `worker_id` 调用 ownership 检查，没有使用已有的 lease-aware 校验。旧 L1 持有 API Key/HMAC 时，在 L2 接管后仍可写状态和日志。

`log_type` 也没有枚举和长度约束。空白类型会先落 PG（被改写为 stdout），提交后的回读再因原 DTO 类型为空报错；HTTP 返回失败后 Worker 重试会复制已提交日志。任意/超长类型还可污染 Redis key 空间。

### P1-SEC-02 Rule 网络隔离与代理设计互斥

`rule_egress.py:13-20` 在宿主 `127.0.0.1` 启动受限代理；`sandbox.py:282-283` 在默认 `allow_network=false` 时创建独立 netns，payload 无法访问宿主 loopback，默认 Rule 实际断网。打开网络开关后不再 unshare，payload 获得 Worker 完整网络，原始 socket/Chromium 可绕过 HTTP proxy。

代理还复用 `git_url_security.py:123-132` 的全局 `ALLOW_PRIVATE_NODES` 解析策略；为内网 Git 打开此开关会同时允许 Rule 访问私网/回环，形成 SSRF。

### P1-SEC-03 沙箱暴露完整文件系统

`sandbox.py:262-304` 使用 `--ro-bind / /`，再靠有限目录黑名单遮蔽凭据。`.env`、`.netrc`、`.git-credentials`、`.npmrc`、shell history、单文件 secret 和自定义挂载仍对同 UID 的不可信 code/rule 项目可读。生产 Compose 还给 Worker 注入了全部共享 secret，使影响进一步扩大。

### P1-SEC-04 真实 Worker 凭据未忽略且权限错误

`services/worker/runtime_data/secrets/worker_credentials.json` 为 untracked，`git check-ignore` 无匹配，目录权限 `0755`、文件权限 `0644`，且包含非空 worker_id/api_key/secret_key。`.gitignore` 的 `/runtime_data/` 只匹配仓库根，未覆盖嵌套路径；实现要求的 `0700/0600` 也未满足。

### P1-SEC-05 Direct Redis ACL 不能提供 Worker 级隔离

`common/security/redis_acl.py:11-40` 已明确记录：ACL 必须给 Worker 任意 `run:owner:*` 的底层写命令、共享 Lease 索引、所有 spider key 和 global control consumer group 权限。失陷 Worker 可伪造 ownership、污染其他 run 数据或 ACK 他人广播。这是非 K8s 部署同样存在的架构风险。

### P1-SEC-06 Gateway 控制和拉取接口缺资源/代际边界

`CancelTask/UpdateConfig` 的 Proto 没有 `lease_id`，服务只绑定认证 Worker 后直接 XADD，不复核当前 Lease；旧代际凭据可向现行 control stream 注入指令，且 XADD 没有 MAXLEN。`StreamTasks.prefetch` 和 `max_concurrent_tasks` 无上限；pending/live 各读 N 条，有效 Worker 可放大 Redis、PEL 和进程内存。AckTask/AckControl 的 Lease 检查与结算也分离，仍有换代 TOCTOU。

### P1-SEC-07 Crawl 授权、状态和告警合同不闭环

`crawl.py:52-63` 等入口不复验当前项目权限；`batch_service.py:177-198` 等状态转换缺 CAS，并发操作可覆盖终态。Web API `crawl.py:1091-1108` 只修改本进程告警单例，Master `alert_check_loop.py:19-21,75-105` 不会收到配置，UI 显示成功但生产告警行为不变。

## 6. P1 SSE、前端认证与功能正确性

### P1-SSE-01 超大首帧使缺口扫描永不推进

`log_stream_gap.py:107-122` 在首帧超过 4 MiB 预算时设置 `truncated=True`，却不更新 `last_id`；`log_stream_active.py:174-181` 因此立即重扫同一行并持续查询 PostgreSQL。HTTP 允许的 1,048,576 个 NUL 编码后形成 6,291,756 字节 SSE 帧，先超过 broker 2 MiB 实时预算触发 overflow，再超过 gap 4 MiB 预算；实测结果为 `emitted=0 truncated=True last_id=0 snapshot=1`。

### P1-SSE-02 sequence=0 的重叠过滤自相矛盾

`sse.py:107-112` 明确 sequence 0 合法，但 `log_stream_replay.py:38-43,63-65,110-113` 只记录大于 0 的 sequence。sequence 0 的历史行与实时行不会被识别为重叠，可能重复展示/导出。

### P1-SSE-03 前端日志内存限制失效并可能错误去重

`enhancedLogViewerUtils.ts:30-39` 把完整 `LogEntry` 保存在 `raw`；`useLogMessageBuffer.ts:93-97` 只截断顶层 content，`raw.message` 仍保留最高 1 MiB 原文。5000 行理论上可保留约 5 GiB，JSON 导出还会再次序列化；实现用 UTF-16 字符数冒充 UTF-8 字节数。

未知日志类型先降级为 system，随后转换丢弃原始稳定 ID，buffer 最终按 `system:sequence` 去重；未知类型与真实 system 使用同一 sequence 时会静默丢一行。

### P1-SSE-04 全局实时事件流没有字节高水位

`infrastructure/redis/sse_event_stream.py:11,43-46` 只设置 `MAXLEN=20_000`，没有单帧或总字节预算。合法 1 MiB 控制字符日志可编码为约 6.29 MiB；极端情况下单个 Redis Stream 可接近 117 GiB。条数上限不能替代字节上限，且 Stream 膨胀会与 PEL/trim 阻塞相互放大。

### P1-SSE-05 非标准异常的毒日志会永久阻塞 PEL

Master `log_ingest_loop.py:206-270` 只把 `InvalidLogBatchError` 送 DLQ；NUL、极值 timestamp 或持久化层抛出的其他确定性异常会继续留在 pending 并反复 reclaim。读取策略持续优先新消息，PEL 在持续流量下还会饥饿；未 ACK 最小 ID 又阻止安全 trim，最终同时拖垮消费和保留。

### P1-SSE-06 Direct/Gateway 日志入口没有统一边界合同

- Direct Redis 路径在 Worker/Master 边界未统一执行单条字节、条数、NUL、timestamp 和 batch_id 校验；同一数据在 Gateway 被拒绝、在 Direct 却进入 PEL。
- Gateway `LogBatch.entries` 没有条数上限，并且在完整物化、构造 run_id set 和查询 ownership 后才做业务字节校验；50 MiB gRPC 帧可先放大内存和 SQL IN。
- HTTP Worker 上报缺稳定 event_id 幂等合同，配合“提交后回读失败”会把重试变成重复日志。

### P1-SSE-07 sequence 分配仍不能保证多进程提交顺序

多 Master/Web API 可在取得 PG 串行锁并 INSERT 之前完成 sequence 分配；进程 A 先分配小 sequence 后暂停，进程 B 分配大 sequence 并先提交，恢复顺序与提交可见性反转。SSE 去重、过滤和按 sequence 展示因此仍可能跳行或乱序；必须让 sequence 分配与同 run 的 INSERT/commit 顺序处于同一数据库串行化边界。

### P1-FE-01 会话撤销和运行访问撤销的作用域处理错误

`logStreamConnection.ts:183-195` 对 `session_revoked` 与 `access_revoked` 都只广播全局 logout。BroadcastChannel/storage 不向发送标签回送，所以会话撤销时当前标签不清 token；仅当前 run 的 access_revoked 又会错误登出其他标签。

### P1-FE-02 会话恢复可卡在半认证状态

`useAuth.ts:66-86` 先取得并写入有效 access token，再加载权限。权限请求失败时 catch 因 `hasLiveAccessToken()==true` 不清 token、不写错误；store 未认证，全局 restore promise 却已 settled，页面可长期处于不一致状态。

### P1-FE-03 三个 Task 静态 GET 路由不可达

`tasks.py:385` 先注册 `GET /{task_id}`，`:524-544` 才注册 `/templates`、`/running`、`/stats`。Starlette 按注册顺序取首个 FULL match，实测三个路径都进入 `get_task(task_id='templates|running|stats')` 并返回 404。

### P1-FE-04 Monitor 继续生成伪日志

`Monitor/data.ts:125-145` 根据 CPU、内存和状态合成“系统健康检查通过”、“Worker 离线”等日志，不是后端事件；这与上一轮文档声称已移除合成监控数据相矛盾，会把推断结果当成真实运维证据。

### P1-FE-05 多个页面仍把局部分页/缓冲伪装成全量数据

- Task 统计只汇总当前第一页约 20 条，Spider 数据固定最多 200 条；
- “完整日志下载”只导出浏览器当前缓冲，不是服务端完整日志；
- Monitor 的详情/筛选入口存在无 `onClick` 控件；手动刷新未 await 完成就提示成功；
- 多个轮询请求缺代际/取消保护，慢旧响应可覆盖新状态；
- 外域 `VITE_API_BASE_URL` 与 nginx CSP `connect-src 'self'` 冲突。

### P1-FE-06 认证、Dashboard 和表单仍有确定性功能错误

| 入口 | 问题与证据 |
|---|---|
| 多标签 refresh | 多标签共享 refresh cookie，但 refresh 会撤销旧 session。第二个标签恢复后，第一个标签的 access token 立即失效；第一个标签的 SSE 再广播 logout，又把持有新 session 的标签登出。证据：`web_api/routes/v1/base.py:448-465`、`frontend/services/auth.ts:457-474`、`frontend/services/api.ts:58-79,121-123` |
| SSE revoke | `session_revoked` 与 `access_revoked` 只广播、不清当前标签；前者让当前标签保留失效 token，后者把仅当前 run 的撤权扩大为全局登出。证据：`logStreamConnection.ts:183-195`、`authToken.ts:98-107`、`useAuth.ts:114-125` |
| Session restore | refresh 已写入 token 后，权限接口失败会留下“有 token、无 user/permissions”的半认证状态；单飞 promise 已结束，不会自动恢复。证据：`api.ts:89-95`、`useAuth.ts:67-85,96-99` |
| 修改密码 | 后端撤销全部 session，前端随后 refresh 必然失败却吞掉错误，当前页继续显示已登录。证据：`Settings/index.tsx:38-52`、`frontend/services/auth.ts:96-114`、`user_service.py:353-355` |
| Dashboard | 普通用户 summary 与 admin-only metrics 绑在同一 `Promise.all`；metrics 403 时 summary 不提交，其他趋势成功又清除整页错误，最终核心统计显示 0、健康显示“异常”。证据：`Dashboard/index.tsx:92-121,161-165`、`dashboard.py:46-54,119-125` |
| Audit filter | `setCurrentPage(1)` 后分页 effect 会立即发送无筛选请求并覆盖筛选结果；后续分页也丢失筛选条件。证据：`AuditLog/index.tsx:114-131,153-180` |
| Project import | 项目列表公开上传导入入口，但 `importProject()` 无条件抛错，所有文件导入固定失败。证据：`ProjectList.tsx:883-889`、`services/projects.ts:410-434` |
| Project drawer | 创建成功时仍为 `loading=true`，`handleClose()` 因 guard 直接返回；下次打开保留旧步骤和表单。证据：`ProjectCreateDrawer.tsx:45-69,124-141,189-213,242-275` |
| Task history | 任务详情只请求默认前 20 条执行记录，再对这 20 条本地分页，旧执行永久不可见。证据：`TaskDetail.tsx:67-80,586-610`、`services/tasks.ts:116-133` |
| Retry count | `retry_count=0` 被 `|| 3` 改写，创建不能提交 0，编辑读取 0 也显示为 3。证据：`TaskCreate.tsx:93-96`、`TaskEdit.tsx:68-71` |
| Crawl result | 抓取结果固定请求 200 条并忽略后端 `last_id` 游标，本地分页只分页这 200 条。证据：`ExecutionLogs.tsx:167-182,685-693`、`services/runs.ts:29-54` |
| Execution race | 轮询、手动刷新与 SSE 终态回查可并发执行 `loadExecution()`，没有取消/代次；旧 running 响应可覆盖 terminal 并恢复轮询。证据：`ExecutionLogs.tsx:88-131,201-212,614-626` |
| Monitor | 只取前 20 个任务却展示为整体统计；详情/筛选按钮无 handler；刷新请求未完成即提示成功。证据：`Monitor/useTasks.ts:27-40`、`TasksSection.tsx:37-46`、`useMonitorController.ts:27-30` |
| Password contract | 设置页允许 100 字符、创建/重置无上限、登录限制 50；后端 bcrypt 对超过 72 UTF-8 字节的后缀不参与验证。证据：`Settings/index.tsx:124-131`、`passwordRules.ts:3-12`、`validators.ts:33-37`、`schemas/user.py:47-51,110-120` |
| User disable | 单用户停用只改 DB、不撤销 session，重新启用后旧 refresh/access 凭据重新有效；批量停用反而显式撤销。证据：`user_service.py:307-328`、`routes/v1/users.py:428-433` |

### P1-FE-07 告警配置与告警历史不是可靠的生产合同

- 前端 `AlertConfigRequest` 字段可选，但后端 `AlertConfigUpdate` 用 default_factory 补齐所有字段，路由再逐项全量写入。保存普通设置会清空 channels；只编辑一个 Webhook 也会重置级别、限流和重试。证据：`frontend/services/alert.ts:55-60`、`schemas/alert.py:60-66`、`routes/v1/alert.py:203-224`。
- 多键配置更新没有事务或版本 CAS；中途异常留下半写状态，并发编辑会互相覆盖（`routes/v1/alert.py:203-267`）。
- 告警历史/统计只存在 Web API 进程内存，真实告警由独立 Master 发出；Web API 默认两个 worker，请求命中不同进程时结果也随机。证据：`alert_service.py:27-31,343-392`、`master/alert_check_loop.py:75-106`、`routes/v1/alert.py:283-311`。
- “测试单渠道”接口接收 channel 和 message，却调用广播全部启用渠道的 `send_test_alert`，且忽略自定义 message。证据：`AlertConfig/index.tsx:534,618`、`routes/v1/alert.py:320-322`、`alert_service.py:244-261`。

### P1-FE-08 Rule/Task 更新合同会固定失败或破坏现有配置

- 规则项目编辑路由的 `request` 无类型、未声明 Body，FastAPI 将其解释为 query；前端发送 JSON，因此请求固定 422。证据：`routes/v1/project.py:1233-1256`、`frontend/services/projects.ts:222-325`。
- 规则项目关闭断点续爬时发送 `undefined`，序列化层省略该字段，后端保留原 true，用户无法关闭。证据：`RuleProjectForm.tsx:343-353`、`services/projects.ts:246-255`。
- `TaskResponse` 缺 `max_instances/timeout_seconds/retry_count/retry_delay/execution_params/environment_vars/success_count/failure_count`。编辑页用默认/空值加载后全量提交，用户只改名称也会覆盖真实执行配置；详情页同时显示 undefined/0。证据：`schemas/task.py:135-168`、`routes/v1/response.py:186-225`、`TaskEdit.tsx:56-74,140-156`。
- 项目统一更新路径没有执行专用 Worker ACL 校验，可绕过正常项目更新的 Worker 使用权限（`unified_project_service.py:72-76,98-115`）。

### P1-FE-09 仓库导入与远端环境存在权限和原子性错误

- 批量导入逐项创建 Project、ProjectCode、ProjectSource 和远端环境，没有事务、补偿、幂等结果或逐项状态；中途失败留下半成品，整体重试会重复。证据：`project_source_service.py:85-110`、`Repositories/index.tsx:119-126`。
- 仓库扫描时增减勾选会重建整个 projects 表单，清空已选项目的自定义名称和共享目录（`Repositories/index.tsx:165-169`、`helpers.ts:3-25`）。
- Shared 环境创建失败被静默当成功，仅 private scope 抛错；普通用户只需 Worker use 权限即可通过仓库导入触发 `create_env`，绕过 runtime 路由的 admin 要求。证据：`project_source_service.py:143-159`、`repositories.py:123-137`、`runtimes.py:82-103`。

### P1-FE-10 Dashboard、Monitor 和下载接口的数据语义错误

- Dashboard 还请求 admin-only `/workers/stats`、`/workers/stats/spider` 和集群历史；Monitor 把可访问的列表与 admin-only stats 放入同一 Promise.all，普通用户每 10 秒 403 并丢弃本可展示的列表。证据：`workers_stats.py:40-66`、`workers_spider.py:49-90`、`Dashboard/index.tsx:92-99`、`Monitor/useWorkers.ts:15-29`。
- “今日完成/异常/成功/失败”实际统计 Task 当前状态全量，没有查询今日 TaskRun，运营语义错误。证据：`dashboard.py:64-83,100-108`、`Dashboard/index.tsx:251-266,320-336`。
- Crawl “完整数据下载”固定 `limit=10000`，超过后静默截断，没有游标续传或 truncated 提示（`BatchList.tsx:298-310`、`services/crawl.ts:163-180`）。

### P1-FE-11 Runtime 创建与编辑的前后端合同损坏

- `runtimes.py:91-110` 接收 scope，却调用 `runtime_control_service.create_env` 时不传 scope；Worker 创建响应也不含 scope（`engine.py:536-547`、`uv_manager.py:438-467`）。前端 `services/runtimes.ts:52-71` 强制 `requireRuntimeScope`，因此远端环境已经创建成功，UI 仍报“缺少 scope”；重试再撞“已存在”。
- `EnvUpdateRequest` 的 key/description 默认 None，路由不依据 `model_fields_set` 区分“缺省”与“清空”；Worker 把 None 解释为 pop。前端 `RuntimeEnv` 又不声明 key/description，transform 把 key 错设为 env.name 并丢 description；任意编辑会把 key 改成环境名并清空 description。证据：`runtime_models.py:40-42`、`runtimes.py:138-157`、`uv_manager.py:374-390`、`envTransforms.ts:7-24`。

### P1-FE-12 Repository、Worker 资源与重试配置存在虚假成功

- Repository 扫描先持有完整旧 ORM 对象，长时间 clone/scan 后用无 `update_fields` 的 `save()` 整行写回；扫描期间并发 PUT 的 name/url/ref/credential/enabled 会被旧值回滚。并发扫描也按最后完成覆盖结果，不代表最新请求。证据：`repository_service.py:55-61,79-133`。
- Worker 资源路由先写 DB，再一次性 XADD；Redis 失败被吞并返回 200/`synced=false`，重连/注册又不会重放 DB desired state。`auto_resource_limit` 虽保存发送但 Engine 忽略，前端忽略 synced 并固定提示成功，TS response 也与后端不一致。证据：`workers_resources.py:132-162`、`engine.py:1612-1636`、`WorkerResourceManagement.tsx:110-125`。
- retry API 接受并回显任意 strategy，但 DB 不保存、Task 无此字段，Master 永远构造 EXPONENTIAL；调用方收到“已更新”但执行行为不变。Redis 异常时 pending retry 又被吞成空列表。证据：`retry.py:119-163`、`retry_loop.py:713-719`、`retry_service.py:590-608`。

## 7. P2 问题

### P2-01 SSE 与日志协议完整性

- 服务端发送标准 SSE `id:`，路由 `log_stream.py:118-154` 却不读取 `Last-Event-ID`，标准 EventSource 重连会被当成无游标连接并全量回放。
- Worker system 日志的 `level/source` 在 realtime/batch、Proto 和 Master 映射中丢失，WARNING/CRITICAL 会按 INFO 展示，过滤和统计错误。
- 暂停期间仅收到状态帧也会被标记成日志 gap，恢复后触发不必要的断开、换票和全量重同步。
- HTTP/Worker 路径使用 `rstrip` 改写日志尾部空格与换行，存储内容不再是 Worker 原始输出；签名/hash/下载与用户看到的内容可能不同。
- history cursor 已到达但 `historical_logs_end` 到达前断线时，新连接不一定重新发 history phase，前端可永久停在“历史加载中”。
- 此前“暂停会永久推进游标并丢日志”的结论不成立：恢复会重建 EventSource；真实残余风险是恢复重放上限导致暂停前旧视图内容被截掉，列为 P2 而非 P1。

### P2-02 其他输入、认证与供应链问题

- Git URL 接受并原样保存/返回 userinfo，`https://user:password@...` 可进入 DB、API 与错误文本。
- Worker API 在验签前按可伪造的 `X-Worker-ID` 限流，攻击者可耗尽指定合法 Worker 的认证桶。
- task trigger 去重 key 使用原始字符串，`1`、`01`、`+1` 可绕过同一任务锁。
- 依赖列表无数量/单项长度上限；通用 create/update 的裸 `execution_params` 可绕过专用 owner、存在性和环校验。
- 批量用户状态把任意 truthy 值转 bool，字符串 `"false"` 会执行启用操作。
- gRPC Logs/Spider 接口先解析大帧并查 DB，再执行 8 MiB 业务尺寸校验；全局 50 MiB 上限允许反复放大 SQL IN 和内存压力。
- Master 错误依赖 PyPI `asyncio==4.0.0`，与标准库同名且无必要。
- 项目、用户、Worker 单删均先不可逆删除后写审计；审计失败会返回 500 但资源已删，批量删除无逐项审计；outbox、Spider tombstone 也没有保留上限。

### P2-03 Master 可假健康

`master/readiness.py:73-92` 只检查 Redis、DB 和少量同步 probe；ResultLoop、LeaseSweeper、reconcile、retry、cleanup 等后台 task 异常退出时仍可持续返回 ready=200。

### P2-04 复杂度门禁并非严格合规

- `PLR1702` 未启用 Ruff `--preview`，stderr 被丢弃，嵌套深度是假绿；启用后有 117 个函数超限，其中生产代码 95 个，最大 8 层。
- 50 行函数规则错误地用 `PLR0915` 的“语句数”代替物理行数。AST 实测有 144 个生产函数超过 50 行，其中 131 个未被门禁发现。
- 前端 ESLint 没有 complexity、max-lines-per-function、max-depth、max-params。临时启用同等规则后有 67 个复杂度超 10、87 个函数超 50 行、6 个嵌套超限、15 个参数超限。
- 当前 966 条 baseline 包含 191 个超长文件、34 个 C901、286 个位置参数超限和 405 个魔法数字。它只证明“债务快照未增加”，不证明满足硬限制。
- 224 条 baseline 指向 117 个未跟踪路径；人工更新 JSON 即可接受新增债务。`tasks.py` 拆分后仍为 554 行，`tasks_transfer.py` 为 534 行，总行数反增 294。
- Core/Master 各有一套超千行 SchedulerService，39 个同名方法；约 5953 行旧 WebSocket、SpiderKit、memory backend 等实现仍残留，持续产生行为分叉。

### P2-05 前端分页、异步状态与可访问性

| 问题 | 影响与证据 |
|---|---|
| Worker 截断 | `getAllWorkers()` 固定 `size=100`，第 101 个以后在列表、监控和选择器中均不可见（`services/workers.ts:34-43`） |
| 用户搜索 | 只过滤当前服务器页；请求可乱序覆盖，跨页选择计数与实际批删目标不一致（`useUserList.ts:23-56`、`UserManagement/index.tsx:62-71`） |
| Monitor 轮询 | Worker/历史指标请求无取消和代次，10 秒轮询短于 Axios 30 秒超时，旧响应可持续覆盖新状态（`useWorkers.ts:15-40`、`useMetricHistory.ts:22-56`） |
| EnvSelector | 快速切换 Worker/scope 时旧请求响应可覆盖新选择（`EnvSelector.tsx:119-139`） |
| 日志导出 | 服务端完整导出入口未接通；按钮仅导出浏览器缓冲和当前筛选结果（`EnhancedLogViewer.tsx:75-86`、`logExport.ts:284-297`） |
| API 独立域 | `VITE_API_BASE_URL` 外域同时受 CSP `connect-src 'self'`、CORS origin 推导和 `SameSite=Strict` cookie 阻断（`apiEndpoint.ts:48-50`、前端 `Dockerfile:66`、`config.py:188-198`） |
| 键盘访问 | Worker 卡片和告警链接使用不可聚焦/无键盘语义的点击容器（`WorkersSection.tsx:17-34`、`WorkersDrawer.tsx:24-42`、`AlertsPerformanceSection.tsx:44-52`） |
| refresh 异常 | 自动 refresh 遇网络/5xx 会清 token 但不清 Zustand store，形成 UI 假登录（`frontend/services/auth.ts:208-221`） |
| RSA 公钥 | 登录公钥永久缓存；后端轮换 `key_id` 后当前 SPA 无刷新/重取机制（`loginEncryption.ts:61-82,107-109`） |
| Project 截断 | 项目列表最多加载前 1000 条，之后用本地筛选/分页并把 filtered.length 当 total；1001+ 项目对搜索和批量操作不可见。Task 创建/编辑选择器又只取第一页 100 条（`ProjectList.tsx:278-360`、`TaskCreate.tsx:47-55`） |
| 权限用户截断 | Worker 权限弹窗只拉用户第一页 `size=100`，无法给第 101+ 普通用户授权（`Workers/index.tsx:334-343`、`users.py:70`） |

### P2-06 Worker/Direct 协议与维护问题

- 同步缩容可在 control loop 内阻塞约 330 秒；这段时间 cancel/kill 无法处理。
- Direct control reclaim 的 generation guard 为 no-op，切代时旧 consumer 可 claim 控制 PEL，新代际需再次等待 min-idle。
- Direct `TaskMessage` 对缺失 `task_id` 静默填空串，Gateway 会拒绝同一帧，两条传输语义分叉。
- `credential/file_store.py` 是完全未使用的旧 `FileCredentialStore`：普通覆盖写、无 `0600/0700`、无原子替换和链接防护、吞错返回 False；它与新的 `PersistentCredentialStore` 冲突。

### P2-07 Crawl 边界与数据完整性

- Crawl 入口允许非 RULE 项目创建批次，后续没有可执行 spider，最终空转。
- 跨 run 扫描达到预算后静默漏项，没有明确 truncated/continuation 合同。
- DELETE test 路径只删数据库测试记录，不删除对应 Redis 结果。
- Direct control、spider 与 ownership 的 ACL/key 布局问题使上述残留数据还能被其他失陷 Worker 修改，不能只依赖 API 权限修复。

### P2-08 审计、保留与死代码

- 项目、用户、Worker 单删均先不可逆删除、后写审计；审计写失败会返回 500，但资源已经删除，调用方重试得到不同结果。
- 批量删除没有逐项审计，无法回答具体哪个对象由谁删除。
- `scheduler_outbox` 无 retention，Spider tombstone 无 TTL，Artifact cleanup 没有单轮批量上限，长期运行会产生表/键膨胀或单轮长事务。
- `worker_project.py`、`worker_project_service.py`、`worker_project_sync.py` 属于未跟踪、未注册迁移 38 的旧实现，不应误报为当前生产外键孤儿；正确问题是约 5953 行旧实现仍污染 mypy、复杂度基线和发布集合。

### P2-09 供应链与镜像发布原子性

- `services/master/pyproject.toml` 锁入 PyPI `asyncio==4.0.0`，与 Python 标准库同名且没有功能必要。
- Docker workflow 先创建正式 tag 再执行 Cosign；签名失败会留下未签名 tag。
- 多服务 matrix `fail-fast:false` 不能保证 Web API、Master、Gateway、Worker、前端作为同一个原子版本集合发布。
- Trivy 使用 `ignore-unfixed:true`；扫描通过只表示忽略了无上游修复的 High/Critical，不等于这些漏洞不存在。
- 前端 lockfile 与 package 声明一致，远端 tarball 均有 integrity；但本轮 `npm audit` 因网络/策略未能重新执行，不能把结果写成“当前无漏洞”。

### P2-10 其余前后端合同缺口

- Rule 表单的 headers/cookies JSON 没有 validator；`handleFinish` 中 `JSON.parse` 失败会直接抛出未处理事件异常（`RuleProjectForm.tsx:383-404`）。
- Dashboard 24 小时趋势只按 `hour` 聚合，跨日期窗口的相同小时被合并；返回顺序固定 0..23 而不是窗口时间序（`dashboard.py:198-227`）。
- 环境页对普通用户显示创建、删除和装卸包按钮，但后端全部要求 admin，形成稳定 403 而不是权限感知 UI（`Envs/index.tsx:140-231`、`runtimes.py:77-105,163-257`）。
- Git 凭证删除不检查 Repository 引用，删除后已有仓库留下不可用 credential_id（`git_credential_service.py:77-82`）。
- `RepositoryUpdate` 以 `None` 表示“未提供”，因此无法通过 `credential_id=null` 解绑凭证（`repository_service.py:91-102`）。
- Worker ACL 管理仍使用裸 dict，不校验 permission 枚举和 user 是否存在；batch assign 不统一解析 public user id、静默忽略不存在 Worker，已有 view 权限也不会升级为 use。模型没有 FK/枚举，能产生孤儿和无效权限行（`workers_permission.py:56-132`、`worker_service.py:562-600,702-740`）。
- Runtime 把 not-found、conflict、timeout、Worker offline 等所有远端错误统一映射为 500，调用方无法区分 404/409/503/504，也无法正确决定是否重试（`runtimes.py:37-39`）。

## 8. 自动化验证

当前脏工作树结果：

| 检查 | 结果 |
|---|---|
| Ruff check | 通过 |
| Ruff format check | 失败：`workers_crud.py` 需格式化 |
| mypy | 100 errors / 23 files / 587 files checked |
| complexity | 表面通过，966 条 baseline；规则覆盖无效，见 P2-04 |
| `uv lock --check` | 通过 |
| Bandit High/High | 0；另有 Medium 28 / Low 77 |
| pip-audit | 149 个锁定包，0 个已知漏洞 |
| 前端 type-check / lint / build | 通过 |
| 前端 Vitest | 20 files / 100 tests passed |
| 前端 npm audit | 先前运行记录为 2 个 moderate；本次隔离网络未能独立复验 |
| Core | 748 passed |
| Web API | 375 passed / 1 warning |
| Gateway + Master | 381 passed |
| Worker + Scripts | 716 passed / 6 platform skips |
| Boundary | 15 passed |
| Lease/ACL/quota/SSE 定向回归 | 55 passed |
| Worker/Master 新增定向复核 | 38 + 48 + 148 passed，均在 60 秒硬超时内 |

上述后端/前端绿灯依赖大量未提交文件，不能代表 `HEAD`。此外真实 Redis 集成仍有 66 项因未配置集成 Redis 而 skip，不能表述为“所有功能已测试”。

本轮未执行：测试机部署、中间件清库、fresh Docker build、真实 PostgreSQL/Redis migration 和故障注入、Redis ACL/Cluster、TLS/mTLS 握手、备份恢复、浏览器真机 E2E、断网/重启/多副本竞态测试及压力测试。

### 8.1 可执行复现与测试盲区

- 干净 `git archive HEAD` 快照独立复现了 Master/Gateway/Worker import 失败、前端 type-check/build 失败和复杂度 checker/baseline scope 冲突。
- Task 静态路由使用 Starlette `APIRoute.matches` 复现：`/templates`、`/running`、`/stats` 的首个 FULL match 均为 `/{task_id}`。
- SSE 大帧使用合法上限内的 1,048,576 个 NUL 复现，编码帧为 6,291,756 bytes，gap progress 保持 `last_id=0`。
- sequence 0 使用真实 replay state 复现，`record()` 后 `overlaps()` 仍为 False。
- Worker、Lease、Master 聚焦测试分别为 48、38、148 passed；这些测试没有覆盖 kill 失败后 control ACK、事务交错、runtime release 异常、结算失败后的下一次重投和 QUEUED 超 TTL。
- 安全/Authz 定向测试 91 项通过；另有 10 项因为本地沙箱禁止 loopback bind 未进入业务断言，不能算通过或失败。
- 真实 Redis Worker 集成 66 项 skip，覆盖的恰是断线恢复、取消、结果、注册和 ACL 路径；单元 mock 不能替代这些协议测试。
- 本轮后端 unit+boundary 合计 2235 passed，前端 100 tests passed；数量不能抵消未覆盖的并发交错、权限角色、分页全量和多标签浏览器行为。

## 9. 问题关闭与验收标准

### 9.1 P0 关闭标准

| P0 | 必须同时满足的证据 |
|---|---|
| 发布闭包 | 将确定的生产文件、迁移、生成 Proto 和前端模块纳入提交；在全新 checkout 中重新安装依赖；四个 Python 服务 import/start；前端 type-check/lint/test/build；复杂度 checker；所有 Dockerfile fresh build 全通过 |
| 非 K8s Compose | 修正 Gateway/Worker TLS 变量；真实 CA/server/client 证书完成 mTLS 握手；Worker 首次注册与 backendless 启动成功；frontend/migration/init/healthcheck/backup/restore 全链路可执行；各服务仅收到自身所需 secret |
| 结算恢复 | 对 result False、result exception、ACK False、ACK exception、Worker crash、同 receipt 与不同 receipt 重投逐一故障注入；最终只能出现一次业务执行和一次持久终态，PEL 必须收敛且不能依赖进程内状态 |

### 9.2 分布式与数据生命周期关闭标准

- TaskRun 终态判断与 lease bind 必须在同一原子条件中，更新谓词显式排除终态；测试应在检查和 UPDATE 之间强制插入终态提交。
- `lease_gen` 采用全生命周期一致的单调域；迁移必须处理 timestamp 存量值，并测试 Redis sequence 丢失、恢复、回滚和滚动升级。
- Direct/Gateway 的 result、log、heartbeat、control、ACK 全部携带并原子验证 `lease_id/lease_gen`，不能只按 worker_id 或在事务外 check-then-act。
- cancel/kill 只有在子进程确定退出或消息被持久化为待重试时才能 ACK；第二次 cancel 必须重试 CANCELLING 中的 kill。
- Worker shutdown 必须逐步骤清理并聚合错误；被 cancel 的 worker tasks 要 await；总预算覆盖两阶段 drain、SIGTERM、SIGKILL 和 transport 注销。
- 项目、任务、用户、Repository、Runtime 的删除/解绑应通过 FK、兼容锁、事务内 outbox 或幂等补偿闭环；任何进程崩溃点都不能留下永久孤儿。
- outbox 的业务成功、永久失败和已消费必须用独立状态表达；必需清理副作用不能因固定五次瞬时失败直接永久结束。
- Artifact quota、日志保留和 cleanup leader 必须在多副本下保持单一语义；Redis 不可达时不得静默降低安全边界。

### 9.3 安全关闭标准

- Worker 沙箱改为最小允许挂载，不再 `--ro-bind / /`；任务网络只能到达独立 egress proxy，原始 socket 无法绕过，Git 私网策略与 Rule SSRF 策略完全分离。
- 从工作树和 Docker build context 排除运行时凭据；目录/文件权限满足 `0700/0600`，并完成凭据轮换，验证历史镜像层和制品不包含凭据。
- Direct Redis 若继续作为生产路径，必须重构 key/协议以实现 run、spider、group 成员级隔离；仅调整 ACL command list 不能关闭该项。
- Git URL 禁止 userinfo；认证限流不得让未验签请求消耗受害 Worker 的身份桶；所有外部列表/字符串/大帧先做数量和字节边界校验再访问 DB/Redis。

### 9.4 SSE 与前端关闭标准

- 任意后端允许的单条日志都必须能让 gap cursor 前进或得到显式终止错误；恢复同时覆盖 sequence 0、未知类型、标准 `Last-Event-ID` 和断线窗口。
- 浏览器缓冲按 UTF-8 实际字节执行单条及总量预算，截断后不能在 `raw` 保留原文；完整导出必须由服务端分页/流式接口实现。
- `session_revoked`、`access_revoked`、refresh rotation、修改密码和多标签恢复分别做真实浏览器 E2E，验证当前标签与其他标签的精确作用域。
- 所有列表统计、下载和本地分页必须明确 total/cursor/truncated；慢响应必须用 abort 或 generation 防止覆盖新状态。
- 普通用户、管理员、跨用户三类角色分别执行 Dashboard、Monitor、Task、Project、Repository、Runtime、Alert 和 Crawl E2E；不能用 admin 单角色绿灯代替授权验证。
- 前后端 schema 采用契约测试，至少覆盖 TaskResponse 完整字段、Rule update Body、alert PATCH 语义、`retry_count=0`、Crawl 游标和 Repository credential 解绑。

## 10. 与既有修复文档的冲突

`docs/round6-remaining-risks.md:128-130` 声称 P0 全部关闭、format/mypy 全过、Monitor 无合成数据。当前干净 HEAD、Compose、门禁和源码实测均直接否定这些陈述；该文档不能继续作为发布验收依据。

## 11. 验收判定

1. 当前版本：**拒绝生产发布**。
2. 当前版本：**拒绝压测**。发布提交和生产 Compose 已有确定性阻断，压测结果无法代表可交付版本。
3. K8s：**正式排除，不作为阻断，也不把历史 K8s 问题记作已修复**。
4. 当前工作树：可用于继续开发和局部回归，不可作为发布制品。
5. “已全部修复、稳定且无错误”：现有代码和验证证据不支持该结论。
