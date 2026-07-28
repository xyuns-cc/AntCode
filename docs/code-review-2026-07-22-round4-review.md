# AntCode 第四轮修复后复审报告（2026-07-22）

> 复审对象：当前未提交工作树，以及 `docs/code-review-2026-07-22-round3-review.md` 后的新一轮修复。
>
> 复审方式：主审配合多代理，覆盖 Gateway/Lease、Direct Redis、任务状态机、数据库事务、日志/SSE、前端、安全、Worker 沙箱、Kubernetes、迁移和供应链。
>
> 本轮性质：只读代码复审与本地自动化验证；未修改业务代码，未连接测试机或执行真实 PostgreSQL/Redis 全链路。

## 1. 执行结论

当前版本仍然**不能签署生产可用，也不应开始压测**。

本轮确认上一轮的一批修复已经真实落地，但“已全部修复”的结论不成立。当前至少有四类直接发布阻断：生产 K8s 画像无法启动/连通、Worker 任务可读取 Pod 内身份凭据、严格复杂度与 migration inventory 门禁失败、Python 依赖存在会被仓库 fail-closed 规则阻断的已知漏洞。此外，Lease 切代、取消/派发、跨存储事务、日志容量、前端核心操作仍有可复现或可构造的 P1 正确性问题。

旧报告的 `P0-01 PATH` 劫持已修复；“`infra/k8s` 只有 `.gitkeep`”也已经过时。当前仓库已有 Kustomize 骨架且能够渲染，但骨架配置之间及其与应用合同不一致，不能运行，更不能视为生产部署画像。

自动化测试的大规模通过证明了稳定顺序下的既有行为，没有覆盖本报告列出的双消费者交错、故障注入、跨存储提交、长期积压、极值消息、浏览器多标签页和真实 K8s/mTLS 场景。任何代码审查也不能证明“零错误”；以下为本轮已确认的剩余问题及证据边界。

## 2. P0 生产阻断

### P0-01 Kubernetes 生产画像不能启动或形成服务闭环

- `infra/k8s/base/configmap.yaml:12-17` 只提供拆分的 host/port/name，应用在 `packages/antcode_core/src/antcode_core/common/config.py:417-429` 强制要求完整 `DATABASE_URL`、`REDIS_URL`。Web API、Master、Gateway 和 Migration Job 都只 `envFrom` 该 ConfigMap/Secret，渲染成功后仍会因缺失必填配置启动失败。
- `docs/k8s-deployment.md:28-35` 与 `infra/k8s/overlays/production/kustomization.yaml:5-8` 要求 `JWT_SECRET_KEY`、`WORKER_INSTALL_KEY`，代码实际读取 `JWT_SECRET` 和 `ANTCODE_WORKER_KEY`（`common/security/auth.py:51-86`、`worker/app/worker_registration.py:26`）。按文档生成 Secret 仍无法认证/注册。
- `infra/k8s/base/networkpolicy.yaml:1-11` 默认拒绝所有流量，却没有 PostgreSQL/Redis ingress policy，也没有 Migration/Backup Job egress policy；业务 Pod 即使具备 egress，目标中间件仍拒绝 ingress，两个 Job 也无法访问数据库。
- Gateway/Worker 只挂载 `/etc/antcode/tls`（`gateway-deployment.yaml:42-45`、`worker-deployment.yaml:43-48`），没有设置代码读取的证书路径环境变量。Gateway 认证开启而 TLS 未生效时会拒绝绑定；`ingress.yaml:30-56` 的 Nginx TLS 终止也不能向 Gateway 透传客户端证书身份。
- Web API 探针是不存在的 `/health`（`web-api-deployment.yaml:39-48`），实际 readiness 为 `/api/v1/health/ready`；Master 探针错误地使用 `9100/healthz`（`master-deployment.yaml:32-49`），实际监听 `8101/health/ready`（`services/master/src/antcode_master/readiness.py:42-63`）。PostgreSQL exec 数组中的 `$(POSTGRES_USER)`/`$(POSTGRES_DB)` 不会由 shell 展开（`postgres-statefulset.yaml:49-56`）。
- Web API、Master、Gateway 开启只读根文件系统，却没有为 `/app/data` 和 `/tmp` 提供可写卷。Worker 强制 UID/GID `10003`（`worker-deployment.yaml:26-29`），镜像文件属于 UID/GID `1000`（`infra/docker/Dockerfile.worker:118-120,150-164`），且卷挂到未使用的 `/var/lib/antcode` 而应用写 `/app/data/worker`，会在身份/运行目录初始化时失败。
- `worker-deployment.yaml:11,72-74` 的多个副本共享同一个客户端证书和一次性安装 Key，identity 又只在 Pod 文件系统/`emptyDir` 中保存，无法为每个 Worker 建立独立且可持久恢复的身份。
- overlay 仍使用 `latest` 和 `antcode/<service>`（`overlays/production/kustomization.yaml:24-33`），与 CI 发布的 `ghcr.io/<owner>/antcode-<service>` 不一致；仓库也没有 frontend Deployment。`backup-cronjob.yaml:25-36` 仅在 `/tmp` 生成文件且明确没有上传，Job 结束即丢失备份。

### P0-02 不可信任务可读取并外带 Worker 身份

`services/worker/src/antcode_worker/executor/sandbox.py:260-300` 仍以 `--ro-bind / /` 暴露容器完整根文件系统，只遮蔽固定数据和常见凭据目录。K8s 把 Worker mTLS 私钥挂在 `/etc/antcode/tls`，该路径不在遮蔽集合；任务还可能读取部署的 Worker 配置及 Direct Redis 凭据，并通过日志或 Artifact 外带。gVisor/Kata 只隔离 Pod 与宿主，不会阻止同一 Pod 内任务读取 Worker 进程可见文件，因此不能修复这一身份边界。

### P0-03 仓库自身发布门禁确定失败

- `.venv/bin/python -m scripts.check_complexity` 返回非零：新增 11 项、恶化 17 项。代表性问题包括 `task_run_service.py` 从 364 增至 401 行、`engine.py` 从 1724 增至 1852 行且 `_control_loop` C901 从 11 增至 12，以及新出现的 4/5 个位置参数、C901=11、文件 307/308/319 行等违规。
- `tests/integration/postgres/test_20260713_migrations.py::test_migration_inventory_covers_every_sql_file` 稳定失败：迁移清单漏登记 `20260722_add_task_run_lease_gen.sql`。
- 当前 lock 固定 `pyasn1==0.6.3`（`uv.lock:2518-2523`）。本轮 `pip-audit` 报 `CVE-2026-59885`、`CVE-2026-59886`，修复版本均为 `0.6.4`；`scripts/fail_on_high_vulns.py` 返回失败。因此 `.github/workflows/ci.yml:471-485` 的 fail-closed 安全任务会阻断 CI。

## 3. P1 高风险代码问题

### 3.1 Gateway、Lease 与 ownership

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-GW-01 | `services/gateway/src/antcode_gateway/services/run_ownership_rpc.py:193-210` 在 Redis fence 成功后才用本机 `time.time()` 生成 `lease_gen`；`run_ownership_service.py:127` 接受 `stored_gen <= new_gen`。 | L1 fence 后暂停，L2 先 bind，L1 恢复时反而取得更大时间值并覆盖回 L1；当前代际 CAS 方向错误。 |
| P1-GW-02 | ownership claim 不读取 TaskRun 终态（`run_ownership_service.py:23-38`）；Worker 是先上报终态再 ACK ready（`engine.py:1147`），Gateway 可 reclaim PEL（`handlers/poll.py:276`）。 | 终态已持久化但 ACK 丢失时，L2 可再次 claim 并执行同一 run，外部副作用无法由终态吸收规则撤销。 |
| P1-GW-03 | Direct L2 只改 Redis ownership（`engine.py:1434`），没有调用 PG lease generation bind；`task_run_service.py:163` 仍拒绝 L1→L2。 | L2 已执行，但日志和结果因 PG 仍绑定 L1 被拒，形成真实副作用与持久状态分裂。 |
| P1-GW-04 | Gateway task ACK/requeue 的 Lease 检查与结算 Lua 分离（`data_service.py:220`、`handlers/task_settle.py:85,180`）；普通 control ACK 同样将检查与 XACK 分离（`control_service.py:181,812`）。 | 切代发生在两步之间且 PEL 尚未换 owner 时，旧 L1 仍可 ACK、重投、DLQ 或吞掉控制事件。 |
| P1-GW-05 | Gateway runtime marker 只要求 Lease Hash `PTTL > 0`（`runtime_control_settlement_store.py:11`），而 Hash 在逻辑到期后仍保留 5 秒；权威 `is_current()` 要求 PTTL 大于 retention（`lease_service.py:615`）。 | L1 可在逻辑过期后的 retention 窗口提交 runtime-control 结果。 |
| P1-GW-06 | self-fence 调用 `cancel_all()`，但 `engine.py:1299` 在 executor cancel 为 False 时仍返回成功，`executor/process.py:663` 还吞掉终止异常。 | transport 认为撤销完成并断开，旧子进程可能继续运行，L2 同时接管并双执行。 |
| P1-GW-07 | legacy settlement 默认值和非法配置都回退为永久开放（`handlers/task_settle.py:18`）。 | 滚动升级完成后配置遗漏/拼错会让裸 `worker_id` 旧通道长期 fail-open。 |

### 3.2 Direct Redis 与 ACL

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-DR-01 | Worker ACL 仍可 HSET/PEXPIRE 自身 Lease Hash，可读取其他 Worker Lease，并可写共享 result/log stream；Master 主要信消息中的 worker/lease 声明。 | 被撤销 Worker 可伪造当前 Lease；任一 Direct Worker 可冒充其他 Worker 上报终态和日志。 |
| P1-DR-02 | ACL 允许裸写共享 `run:owner:*` 和共享 Lease 索引，并允许对 global control stream 任意 `XGROUP CREATE`（`redis_acl_policy.py:100-124`）。 | Worker 可删除/延长他人 ownership，或创建孤儿 group 钉住 stream 裁剪并耗尽 Redis。 |
| P1-DR-03 | ACL 密码轮换没有行锁/CAS；Redis `SETUSER` 与 PG 密文保存跨存储分离（`common/security/redis_acl.py:95-124,183-191`）。 | 并发轮换可留下 Redis 密码 B、PG 密码 A，Worker 永久失联或遗留孤儿账号。 |
| P1-DR-04 | Direct task/control settlement 仍先独立校验 Lease，再执行只校验 consumer 的 Lua（`redis/transport.py:494,1174`、`task_settlement.py:15`、`owned_stream_ack.py:7`）。 | 与 Gateway 相同，切代可插入检查和结算之间，旧 L1 越权确认。 |
| P1-DR-05 | control recovery 首轮未达到 min-idle 的 PEL 不在后续正常恢复路径中；TaskStatus 又使用 `XADD *`，没有稳定事件 ID。 | 旧控制事件可能永久遗漏；响应丢失会生成重复状态、重复结果甚至假 DLQ。 |
| P1-DR-06 | `scripts/migrate_lease_keys.py:203-216` 重建 active 索引时不比较 Redis TIME、逻辑 `expires_at_ms` 和有效 PTTL。 | retention 窗口中的逻辑过期 Lease 会被重新加入 active。 |

### 3.3 取消、派发、重试与恢复

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-SM-01 | 单 run cancel 已修，但 task 批量 cancel、stop 和 crawl cancel 仍基于 `DISPATCHING + worker_id=NULL` 陈旧快照决定不发 control；Crawl batch cancel 也只取一次 active-run 快照。 | 快照后完成绑定/新建的 run 可在数据库已取消后继续执行。 |
| P1-SM-02 | Worker dequeue 后 cancel 的 queue remove=False 和 `CANCELLED -> PREPARING` transition 失败仍被忽略；cancel=False 仍可按成功 ACK。 | 已取消任务可启动进程，且控制面错误显示取消成功。 |
| P1-SM-03 | cancel tombstone 仍是进程内 600 秒并在结算前单次 pop。 | 重启、超时、ACK 失败或重投会失去取消 fence。 |
| P1-SM-04 | Redis MULTI/EXEC 透明重试可重放 XADD，外部尾扫只检查 512 条；派发 bind 只改 `worker_id`，不原子消耗状态。 | 响应丢失或双 dispatcher 竞态会把同一 run 派发/执行两次。 |
| P1-SM-05 | FAILED 重派不复位 dispatch 终态；结果 Lease/Worker 校验在事务外，状态 CAS 没有完整 expected lease/worker；事务内语义冲突直接 return False。 | 新 run 可执行但 PG 永久 FAILED；L1 可在 L2 改绑后结算；部分状态更新还可能被提交。 |
| P1-SM-06 | result/log PEL 处理优先新消息；持续新流量时旧 pending 永久饥饿。 | 旧结果、日志或控制事件可能永不恢复。 |
| P1-SM-07 | 所有 Master 无 leader gate 地执行启动恢复；恢复逐条提交并吞错，startup/retry/no-ack 等扫描使用固定 limit 且无分页。 | 多 Master 可并发误判、重复执行；头部坏项或大积压会让后续条目永久饿死。 |
| P1-SM-08 | Master 异常、timeout、reconcile 判死、Lease eviction 等多个失败源仍不创建 durable retry intent。 | 同一 retry policy 因失败来源不同而失效，任务永久停在失败态。 |

### 3.4 数据事务与生命周期

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-DB-01 | standby Master 也消费 `task_trigger`，只把任务放进本机 APScheduler 就标 outbox consumed；持久 TaskRun 之后才创建。 | standby 消费或 leader 在 job/run 之间崩溃会永久丢触发。 |
| P1-DB-02 | Artifact cleanup 使用 statement snapshot + 跨表 `NOT IN` + KEY SHARE；snapshot 表无 FK，新增引用不会让旧 snapshot 重评。 | 并发 source snapshot 写入成功后，对应 artifact 仍可能被删除并悬空。 |
| P1-DB-03 | Task/Project 主事务先提交实体删除，再调用日志 purge（`scheduler_service.py:441-443`、`project_cascade_delete.py:51-62`）。 | 崩溃或瞬时 DB 错误会得到 API 5xx 但实体已删除；客户端重试无法再次触发 purge，孤儿日志永久化。 |
| P1-DB-04 | 项目删除漏掉 `task_id=0` batch run、相关日志/snapshot 和多类无 TTL Redis 数据；bundle writer 又不锁/校验 Project，可在删除提交后写回无 FK snapshot。 | 删除不完整，并允许 late writer 重建孤儿 run/snapshot。 |
| P1-DB-05 | `/workers/dispatch/task` 在事务外校验 Task，再创建无 FK TaskRun。 | 与 Task/Project 删除并发时可新建永久孤儿 run。 |
| P1-DB-06 | HTTP batch logs 按组独立提交，整体失败返回 503，且 entry 无稳定 event_id。 | 客户端重试会复制已经成功的组。 |
| P1-DB-07 | outbox 第五次失败直接写 `consumed_at`，与成功没有状态区分，只依赖有限 DLQ。 | 控制事件、触发或删除清理可能被永久放弃且难以审计恢复。 |
| P1-DB-08 | 新 `20260722` migration 在事务内普通 `CREATE INDEX`；`scripts/init_db.py` 仍漏 `api_key_previous_expires_at`，升级文档漏旧 Worker 凭据 SQL/回填。 | 大表迁移会阻塞写入；旧库可能初始化成功后运行时报缺列或 Worker 认证失效。 |
| P1-DB-09 | Gateway crawl batch 固定 `task_id=0`，SpiderData 权限检查强制查询真实 Task；snapshot 的空 `subdir` 又用 `or current_source.subdir`。 | Gateway 模式 batch SpiderData 必然 `PERMISSION_DENIED`；同 run 重试的源码路径可能漂移。 |

### 3.5 日志、SSE、安全与容量

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-LOG-01 | 主子进程日志链 `executor/process.py:567 -> logs/manager.py:225` 绕过脱敏器，命令 argv 也会原样记录。 | token、密码和参数可进入 Redis、PG、下载与浏览器。 |
| P1-LOG-02 | HTTP 日志读取/下载可一次加载 10,000 行，每行允许 1 MiB；处理链还会 split/join/encode/复制。 | 单请求理论产生约 10 GiB 级内存放大并 OOM。 |
| P1-LOG-03 | SSE 25 行页在字节预算前物化，Unicode 极值约 100 MiB/连接；legacy Redis history 可一次取 200 个 8 MiB 消息，理论约 1.6 GiB。 | 少量合法请求即可耗尽 Web API 内存。 |
| P1-LOG-04 | recovery 每轮 COUNT 全余量却只回放 10,000 行/8 MiB；broker 可连续 drain 1000 条且无权限 guard、字节或时间预算。 | 长缺口接近二次扫描；会话撤销后仍可能继续输出，慢连接拖垮事件循环。 |
| P1-LOG-05 | 极值 Proto timestamp 抛普通异常而不进 DLQ；ingest stream 无高水位/backpressure；持续新流量时旧 PEL 饥饿。 | 毒消息反复阻塞、Redis 无界增长、旧日志永久不落库。 |
| P1-LOG-06 | HTTP 日志入口无 Lease、幂等键和封闭 log_type；未知类型降为 system 后只按 `system:sequence` 去重。 | 旧代际可注入日志；重试复制，未知类型与真实 system 同序号时静默丢行。 |
| P1-LOG-07 | 前端仅按 5000 行限制，无字节预算；`TaskStatus.result_data` 可跨帧持续 merge；Artifact quota 是进程内 LRU；项目导出不限制 TaskRun/result_data 总字节。 | 浏览器、PostgreSQL 和 Web API 均可被合法大对象持续放大。 |

### 3.6 前端功能与合同

| ID | 问题与证据 | 影响 |
|---|---|---|
| P1-FE-01 | access token 只在标签页内存中；新标签 refresh 会旋转并撤销共享 refresh session，旧标签 401 后直接广播 logout。SSE 又把正常 rotation 导致的 `session_revoked` 当永久停流。 | 多标签页互相踢下线；token rotation 后日志流可能永久停止。 |
| P1-FE-02 | `authToken.ts:13-18` 用普通 `atob` 解 JWT payload，没有 Base64URL、padding 和 UTF-8 处理；`api.ts:52-68` 将解码失败视为即将过期。 | Unicode 用户等合法 JWT 可触发每请求 refresh，形成 session rotation 风暴并放大 P1-FE-01。 |
| P1-FE-03 | 修改密码后后端撤销全部会话，但前端保留已登录状态且无成功反馈。 | 用户看到假登录状态，后续请求才突然失败。 |
| P1-FE-04 | `ExecutionLogs.tsx:69-106` 不校验 taskId/runId 归属；加载函数吞掉失败，刷新仍提示成功；Artifacts/SpiderItems 失败被置空，SpiderItems 固定只取前 200 条（`:121-165`）。 | 错链路显示错误 run，失败被冒充为“没有数据/刷新成功”，抓取数据静默截断。 |
| P1-FE-05 | Monitor 将普通用户可用的 worker list 与 admin-only aggregate/history 放在同一加载链（`Monitor/hooks/useWorkers.ts:18-23`、`useMetricHistory.ts:22-30`）。 | 普通用户因 403 丢掉已成功的 Worker 列表，页面错误显示空集或系统正常。 |
| P1-FE-06 | Monitor 每 30 秒拉取 720 小时原始心跳（`useMetricHistory.ts:33-55`）；后端 `.all()` 无上限，图表无 decimation。 | 默认心跳可达约 86,400 点/Worker/请求，请求叠加并冻结浏览器/压垮 API。 |
| P1-FE-07 | Monitor “Worker 日志”由 `Monitor/data.ts:125-145` 合成“系统健康检查通过”等文本，并非真实日志。 | 运维页面伪造成功信号，掩盖真实故障，违反 fail-explicit 原则。 |
| P1-FE-08 | 仓库导入服务错误使用 `/api/v1/projects/import-from-repository`（`services/repositories.ts:30-34`），后端只有 `/api/v1/repositories/import-from-repository`；项目列表仍提交已下线的 `/api/v1/projects/import`，后端固定返回 410。 | 两个用户可见的项目导入主流程分别必然 404 和 410。 |
| P1-FE-09 | `enhancedLogViewerUtils.ts:50-55` 即使搜索文本为空也对每条完整日志执行 `toLowerCase()`，每批消息都会对整个缓冲重算。 | 大日志和高吞吐下持续产生 O(缓冲总字节) CPU 与临时内存压力。 |

## 4. P2 中风险、可维护性与测试缺口

### 4.1 分布式与数据

- outbox 无 retention，artifact cleanup 无批量上限；多个时间线仍混用数据库时钟和进程时钟。
- mTLS 证书没有 CRL/OCSP/serial denylist；删除 Worker 不撤 Gateway Lease，旧证书仍可重新续租。Gateway `GRPC_HOST` 配置被 `[::]` 硬编码监听忽略。
- StreamTasks `prefetch` 无服务端上限，pending/live 各取 N，单轮可达 2N；control stream 也没有保留上限。
- Rule 任务默认断网后无法访问父进程 loopback egress proxy；开启网络则得到完整 Worker 网络。进程 rlimit 不约束子孙进程聚合 CPU/内存/磁盘，Go 依赖预取还在沙箱外继承 Worker 网络/HOME。
- stdout 日志字节上限未真正生效，背压路径可静默丢日志；终止异常仍可能被误报为取消成功。

### 4.2 前端

- Monitor 的 Worker/周期/任务请求无 Abort 或 request generation，旧响应可覆盖新选择；手动刷新未 await 就提示成功，失败也更新 `lastChecked`。
- Monitor 只统计前 100 Worker/20 Task，状态映射遗漏 cancelled/timeout 等真实状态，selectedWorker 保存陈旧对象快照；磁盘 95% 可同时显示告警、警告数 0 和“系统正常”。UTC 无 offset 的历史时间在上海时区偏 8 小时，“详情/筛选”按钮无处理器，任务状态点按数组下标伪造。
- 会话恢复在 refresh 已成功但权限请求失败时可能停在无用户状态；普通登录的权限请求失败又静默降级为空权限，两条路径语义不一致。日志窗口达到 5000 行后自动滚动失效，错误 notice 可被历史重放清除或被类型过滤隐藏，五次重连后永久停止。
- User Management 搜索只过滤当前页；分页/排序/刷新存在迟到响应覆盖；跨页选择数与实际批删对象不一致；page size 会触发双请求；本地与服务端双重排序。
- 用户资料与角色是非原子两步写；删除末页末条不回退页码；三个 modal 无 submitting 锁；公共 `services/users.ts` 写类型与后端合同漂移；前端用户名规则拒绝后端已允许的 Unicode 用户。
- 创建 Rule 项目时 UI 收集 `region`，但 FormData 和后端 create data 都未传递；外置 API/品牌 URL 与生产 Dockerfile 固定 `connect-src/img-src 'self'` 冲突。

### 4.3 部署、供应链与质量

- 正式 tag 在 Cosign 签名前创建；签名失败会遗留可拉取的未签名正式标签。多服务 matrix `fail-fast:false`，发布不是整套原子操作。
- 候选架构扫描后重新构建 multi-arch，不能证明最终 child 与扫描对象一致；`ignore-unfixed:true` 继续放行无修复高危漏洞；独立 `security-scan.yml` 不在镜像发布 needs 中。
- fresh Compose 文档仍直接 `up` 空库；Makefile Docker 命令没有指向仓库实际 compose 文件名。
- 严格复杂度 baseline 的理念合理：阻止新增和恶化、允许存量逐步收敛。但当前工作树已新增/恶化且 CI 明确失败，不能更新 baseline 来掩盖；`engine.py` 1852 行等存量也远超 300 行硬规则。
- K8s `kubectl kustomize` 只证明 YAML 能渲染；本轮 client dry-run 因没有集群 OpenAPI 上下文仍尝试连接 `localhost:8080`，未形成 server/schema 校验证据。`npm audit` 因包镜像 DNS 不可达，本轮也没有新鲜结果。

## 5. 已确认真实关闭的问题

以下修复在当前工作树中成立，不应回退：

- Worker 沙箱启动器已解析并强制使用绝对路径，任务环境不能再通过 `PATH` 劫持 `bwrap`。
- Gateway 重连取得新 Lease 后会 self-fence；`StreamStatus` 已逐帧校验 current Lease；迟到非终态状态会被拒绝。
- Direct control reclaim 的 `min_idle=0` 已改为正值；FAILED CAS 导致重复 retry 的核心路径已修复。
- `/runs/{run_id}/cancel` 对尚未绑定的 DISPATCHING run 已修复；其他批量/stop/crawl 入口仍见 P1-SM-01。
- SSE active gap 已由 OFFSET 改为 keyset 分页；前端未知日志类型已有稳定 ID，旧的跨类型稳定 ID 冲突已关闭，但 HTTP 未知类型降级后的 `system:sequence` 冲突仍见 P1-LOG-06。
- 登录恢复中的部分认证状态问题已修复；K8s 资源骨架已补齐并能被 Kustomize 渲染。

## 6. 自动化验证结果

| 检查 | 本轮实际结果 | 判定 |
|---|---|---|
| Core CI 分片 | 683 passed / 11.30s | 通过 |
| Web API CI 分片 | 369 passed | 通过 |
| Gateway + Master CI 分片 | 341 passed / 42.69s | 通过 |
| Worker + Scripts CI 分片 | 691 passed / 6 skipped | 通过 |
| Boundary | 15 passed | 通过 |
| Integration collect-only | 157 collected | 仅收集通过，不代表真实集成通过 |
| PostgreSQL migration inventory | 1 failed / 0.13s | **失败：漏登记 20260722 migration** |
| Loadtest 自检 | 21 passed / 9 deselected | 只验证工具，不是业务压测 |
| Ruff check / format | 通过；927 files formatted | 通过 |
| mypy | 537 source files / 0 errors | 通过 |
| 严格复杂度 | 11 NEW + 17 WORSE，返回 1 | **失败** |
| pip-audit + fail-closed | pyasn1 两项 CVE，阻断脚本返回失败 | **失败** |
| 前端 lint / type-check / Vitest / build | 通过；96 tests | 通过，但没有覆盖本报告运行时合同/竞态 |
| Kustomize | production overlay 渲染成功 | 仅 YAML 渲染；P0-01 仍阻断运行 |
| Remote Compose | 两套组合在提供环境变量后解析成功 | 仅配置解析，不是 fresh E2E |
| `git diff --check` | 通过 | 通过 |

本轮没有连接测试机、真实 PostgreSQL/Redis，也没有执行 fresh Compose、浏览器 E2E、真实 Gateway mTLS、故障注入或压力测试。24 个需要 `TEST_DATABASE_URL` 的 PostgreSQL 用例没有执行。不能把 collect-only、配置解析或 loadtest 自检表述成全链路验收。

## 7. 验收判定

1. 当前版本：**拒绝生产发布**。
2. 当前版本：**拒绝压测**。部署、身份隔离、分布式正确性和 CI 已有确定阻断，压测结果不具备上线判定价值。
3. “已全部修复”应撤销；应以本报告的 P0/P1 及其竞态、崩溃、容量回归场景作为下一轮修复和验收基线。
4. 只有 P0/P1 关闭、仓库 CI 全绿、测试机 fresh 环境通过真实中间件全链路和故障恢复后，才具备重新评估压测与生产发布的前提。
