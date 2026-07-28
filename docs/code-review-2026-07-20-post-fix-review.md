# AntCode 修复后独立复审报告（2026-07-20）

> 复审对象：当前未提交工作树，以及 `docs/code-review-2026-07-20-fixes.md` 的全部修复声明。
> 本轮采用主审 + 多代理只读复审；未修改业务代码，未把修复说明当作验收证据。

## 1. 结论

当前版本**不能认定为“全部修复、没有任何问题”**，也**不能进入生产发布或压测**。

修复文档中的一批关键修复已经真实落地，但本轮仍确认：

- 2 个既知 P0 生产阻断项仍未处理。
- 多个 P1 代码缺陷仍可导致旧 Lease 越权结算、任务双执行、取消穿透、日志丢失、数据悬空、SSE 恢复失败或资源耗尽。
- CI 当前不是可发布状态：Integration 在收集阶段失败，Worker 分片稳定超过 60 秒，fresh Compose E2E 缺数据库初始化。
- 普通 Ruff、mypy、复杂度和前端门禁通过，但复杂度 baseline 被整体重置为 901 项，不能证明仓库满足硬复杂度规则。
- 本机 Contracts 只有 86 项通过；43 个 Redis 变体因 `localhost:16379` 不可达而报错，不是“已通过”。

由于已确认代码与门禁阻断，本轮**没有同步测试机、没有重建远程环境、没有执行远程 E2E 或压测**。

## 2. 已确认修复成立

以下高风险修复已在当前代码中确认，不应继续沿用旧报告的原结论：

- PostgreSQL 日志写入已按 run 获取 transaction advisory lock，单 run 的 ID 分配/提交顺序得到串行化。
- Gateway client-streaming 已通过 `_metered_requests` 实现逐帧限流。
- `AckTaskRequest` 已加入 `lease_id`；`StreamTasks` 已增加读取后的第二次 Lease 检查。
- Gateway requeue/DLQ 已改为 Redis Lua 原子结算；正常 ACK 支持响应丢失后的幂等重试。
- Worker 日志已生成稳定 `batch_id`；Direct/Gateway 已共享按 protobuf 字节预算拆批逻辑。
- Direct ownership 已复用 transport 自身的 Redis client 和 namespace；坏 task/control 帧已有隔离出口。
- SSE active gap 已加入基本行数/字节分轮；capacity key TTL、broker shutdown 接线已存在。
- 前端恢复失败不再因 `run_status` 清零熔断；后台 RAF 停止时不再触发全量历史重连风暴。
- 项目导出已沿用任务 owner 过滤，旧的跨用户执行结果越权已修复。
- Redis client 已增加连接互斥、陈旧 client 关闭和健康任务幂等启动。
- RunSourceSnapshot 已改为 `get_or_create` 首写胜，旧的 `update_or_create` 覆盖问题已修复。
- Proto `.py/.pyi` 相对导入、`py.typed`、grpcio 生成版本和包依赖下限已对齐。

## 3. P0 生产阻断

### P0-01 缺少生产部署画像

`infra/k8s/` 仍只有 `.gitkeep`。仓库没有可执行的生产 Ingress/TLS、Gateway mTLS Secret、NetworkPolicy、PDB、多副本、滚动升级、Migration Job、HA、备份与恢复资源。`infra/docker/docker-compose.remote.yml:21-37,93-105` 明确是 HTTP、非 Secure Cookie、明文 Gateway 的测试画像。

### P0-02 不可信任务隔离边界仍不成立

`services/worker/src/antcode_worker/executor/sandbox.py:250-334` 使用 `--ro-bind / /`，未遮蔽 `/run/secrets`、Kubernetes service account 或任意自定义 Secret mount；`infra/docker/docker-compose.remote.yml:129-134` 同时授予 `SYS_ADMIN` 并关闭 seccomp/AppArmor/system-path 保护。Rule 任务在 `plugins/rule/plugin.py:90-100` 明确允许网络，bwrap 因此不执行 `--unshare-net`。该模式不能作为不可信多租户生产隔离。

## 4. P1 高风险代码问题

### 4.1 Gateway、Lease 与日志结算

1. **P1-GW-01：AckTask Lease fence 仍是 check-then-act。** `data_service.py:220` 先查 Lease，`:240` 才结算；`poll.py:351-376,402-496` 的 XACK/Lua 不包含 Lease key/id。切代发生在两者之间时，旧代际仍可 ACK 或 requeue。
2. **P1-GW-02：run ownership 会把 PostgreSQL 留在旧代际。** `run_ownership_rpc.py:102-105` 先绑定 `TaskRun.lease_id`，`:189-207` 才执行 Redis fence。切代命中 `LEASE_STALE` 时 PG 已绑定旧 Lease，新代际会被 `run_ownership_service.py:61-62` 永久拒绝。
3. **P1-GW-03：Lease 过期后静默换代，不会中止旧任务。** `lease_service.py:166-178` 会签发新 generation；Gateway transport `transport.py:1007-1020` 和 Direct `redis/transport.py:957-974` 无条件覆盖 `_lease_id`。L1 任务继续运行，最终却携带 L2 结算，与 PG 中 L1 绑定冲突。
4. **P1-GW-04：Gateway 已 ACK 的合法日志可因消费时 Lease 过期而永久 DLQ。** Gateway 在 `handlers/logs.py:158-170` XADD 后确认；Master 在 `log_ingest_integrity.py:51-90` 再要求 Lease 当前有效，失败后 `log_ingest_loop.py:232-237` 将消息 DLQ 并 ACK。正常注销、积压或 Lease 到期都会丢尾部日志。
5. **P1-GW-05：`batch_id` 合同未闭环。** Gateway/Master 接受最多 128 字符（`handlers/logs.py:172-183`、`log_ingest_message.py:21-37`），但随后拼 `:<index>` 写入仅 128 字符的 `task_logs.event_id`（`task_log.py:41`），可形成永久 PEL。服务端也不重算 SHA-256；相同 `batch_id` 携带不同内容会被 `ON CONFLICT DO NOTHING` 静默吞掉并发布旧行。

### 4.2 Direct Redis

1. **P1-DR-01：task/control settlement 的 Lease fence 仍有 TOCTOU。** `redis/transport.py:497,653,1222` 在 Lua/XACK 前单独检查 generation；`task_settlement.py:15-73`、`owned_stream_ack.py:7-37` 不读取 Lease。切代窗口内旧 Worker 仍可结算。
2. **P1-DR-02：跨 Worker ownership 最长阻塞 3900 秒。** `engine.py:1287-1290` 使用 3600+300 秒 TTL；`run_ownership_fence.py:68-78` 不根据原 owner 的 Lease 状态允许其他 Worker 接管，节点崩溃后同 run 恢复可停滞约 65 分钟。
3. **P1-DR-03：Lease 迁移不是崩溃可恢复的。** `migrate_lease_keys.py:105-120` RESTORE 后立即删除旧 key，`:181-205` 最后才重建索引。DEL 后、索引重建前崩溃，重跑扫描不到已迁移 target，Lease 永久缺 active/expiring 索引。
4. **P1-DR-04：runtime-control deadline 使用跨机器本地时钟。** Master `runtime_control_service.py:73-83` 用本机 `time.time()` 生成绝对过期时间，Worker `engine.py:56-65` 用另一台机器 wall clock 判断；时钟偏移会执行已过期的安装/删除操作或拒绝有效操作。

### 4.3 取消、派发、重试与生命周期

1. **P1-FN-01：取消不是派发 fence。** Master `scheduler_loop.py:1072` 忽略 dispatch CAS 返回并继续派发；Worker 在任务尚未进入本地 state 时取消返回 False（`engine.py:1224`），control loop 在 `engine.py:427` 仍 ACK。批量取消 `tasks.py:794` 也忽略 CAS 结果。
2. **P1-FN-02：派发 XADD 响应丢失会双执行。** `streams.py:166` 使用自动 Stream ID；服务端已提交但 pipeline 响应丢失时，dispatcher 判失败，`scheduler_loop.py:929,1115` 又创建 retry run，原 run 与新 run 均可执行。
3. **P1-FN-03：批量分发可重派终态/运行中历史 run。** `workers.py:1466` 只校验 run 属于 task；`worker_dispatcher.py:878` 无状态守卫地重写 Worker 并再次入队。外部副作用会再次发生，而新结果可能被终态闸门拒绝。
4. **P1-FN-04：Worker 删除与派发没有共同锁。** 删除只锁 Worker（`worker_service.py:321`），派发只写无 FK 的 `TaskRun.worker_id`（`worker_dispatcher.py:878`、`task_run.py:58`）。复查后仍可并发绑定已删除 Worker。
5. **P1-FN-05：pending retry 取消存在 claim 穿透。** Master 持 source 行锁创建新 run（`scheduler_loop.py:1000`），之后才清 intent（`retry_loop.py:656`）；并发取消等待锁后仍会返回成功，但新 run 已开始派发。
6. **P1-FN-06：ResultLoop 积压会误杀合法长任务。** RUNNING 写入的 transport ACK 不代表 PG 已更新（Gateway `transport.py:639`）；PG 仍为 `DISPATCHED + runtime NULL` 超过 180 秒时，`reconcile_loop.py:230` 将其置失败，真实 Worker 仍在执行。
7. **P1-FN-07：项目删除没有移除 Master 内 APScheduler job。** `relation_service.py:263-317` 批量删 Task，但没有为每个任务发送 `task_changed`；Master 只在 `scheduler_event_loop.py:388-393` 收到该事件时移除 job。周期 job 会持续报错直到 Master 重启。

### 4.4 数据与事务

1. **P1-DB-01：Scheduler outbox 仍可能重复非幂等副作用。** `scheduler_event_loop.py:324-329` 先执行 `task_trigger` 再标 consumed；崩溃窗口会重放。`outbox_service.py:185-217` 的 failure requeue 又不校验 claim owner，可清掉另一个消费者的有效 claim。`trigger_task` 不接受 outbox_id 幂等键。
2. **P1-DB-02：Artifact cleanup 仍可生成悬空 snapshot。** CTE 的 `FOR UPDATE` 只锁 `source_artifacts`（`artifact_cleanup_service.py:79-94`）；`RunSourceSnapshot.artifact_id` 是无 FK 的 BigInt（`run_source_snapshot.py:14`），`get_or_create` 不取得父行 key-share 锁。并发复用旧 artifact 时，cleanup 可删掉 metadata/chunks 后让 snapshot 提交。
3. **P1-DB-03：项目/任务删除与在途日志提交不串行。** 删除路径清完日志和 TaskRun 后，已通过完整性校验的日志仍可在独立事务提交；`task_logs` 无 FK，最终重新产生不可达孤儿日志。

### 4.5 SSE 正确性与容量

1. **P1-SSE-01：合法日志类型会使单个 run 永久恢复失败。** HTTP 路径接受任意 `log_type`（`workers.py:91-101`）；Proto 默认 `LOG_TYPE_UNSPECIFIED` 被 `log_ingest_message.py:68-75` 持久化为 `unspecified`。前端 `logStreamProtocol.ts:3-5` 拒绝该值，并在记录 cursor 前断流（`logStreamConnection.ts:138-155`）。
2. **P1-SSE-02：字节预算不能限制查询物化。** gap/history/recovery 先一次性读取最多 200 行（`log_stream_gap.py:69-80`、`ingest_history.py:164-207`、`ingest_recovery.py:56-69`）再检查预算。HTTP 单行按 1,048,576 字符而非字节限制，合法 Unicode 可接近 4 MiB；一页可接近 800 MiB/连接，并超过 broker 2 MiB 队列。
3. **P1-SSE-03：active gap 多轮补发会饿死 broker。** `log_stream_active.py:114-132` 在 backlog 时把 gap deadline 设为 now 后直接 continue，连续多轮完全不调用 broker。高吞吐下队列会 overflow，原恢复活锁未闭环。
4. **P1-SSE-04：gap 分轮接近二次扫描。** `log_stream_gap.py:45-57` 每轮 COUNT 全部剩余记录，再只取 2000 行。100 万行缺口单订阅约扫描 2.505 亿个索引项，多订阅继续线性放大。

### 4.6 安全与资源边界

1. **P1-SEC-01：Worker 日志脱敏 fail-open。** `logs/streamer.py:20-25` 捕获脱敏模块导入的任意异常后原样返回日志，token/password 可进入 Redis 和 PG。
2. **P1-SEC-02：Direct Redis ACL 可绕过 ownership fence并横向操作共享键。** `redis_acl.py:101-126` 允许直接 SET/DEL/PEXPIRE 任意 run owner，还允许修改共享 Lease 索引、消费其他 Worker 的 global control group、写其他 run/project 的 spider key。
3. **P1-SEC-03：项目日志导出没有总字节预算。** `project.py:648-680` 最多加载 200 runs x 200 行，再构造完整 JSON/YAML、encode 和 BytesIO；按 1 MiB/行可接近 40 GiB，并产生多份内存副本。
4. **P1-SEC-04：Gateway 上传/状态没有 run 级资源配额。** Artifact 只有单文件 100 MiB 限制，无 per-run 数量/总量/并发限制；TaskStatus 可在 50 MiB gRPC 上限内原样进入 Redis/PG。持有有效 Lease 的受控 Worker 可耗尽存储或内存。

## 5. P1 CI、迁移与供应链阻断

1. **P1-CI-01：Integration 当前无法收集。** `tests/integration/gateway/test_runtime_control_result.py:17` 导入已删除的 `_settlement_key`。复现结果为 `155 tests collected, 1 error`，直接阻断 `.github/workflows/ci.yml:123-128`。
2. **P1-CI-02：Worker CI 分片稳定超过 60 秒。** `.github/workflows/ci.yml:112-113` 的原命令本轮复现到约 55% 后退出 142。两个 settlement 测试真实 sleep 约 30 秒，Lease 用例约 28 秒，Gateway 启动用例约 10 秒。
3. **P1-CI-03：fresh Compose E2E 没有初始化数据库。** `.github/workflows/ci.yml:165-190` 直接 `compose up`；Web API `lifespan.py:139-143` 只建连接，不建表。README 反而在 `infra/docker/README.md:132-135` 明确要求先运行 `scripts.init_db`。
4. **P1-CI-04：生产支持的 Gateway/backendless/TLS 链路没有真实 E2E。** CI 在 `ci.yml:215` 固定验证 Direct；Gateway contracts 使用 fake server，不执行生产认证、Lease fence、限流和 ACK 逻辑。
5. **P1-CI-05：既有集群升级清单遗漏 20260720 migration。** `docs/database-setup.md:90-104` 没有执行 `20260720_add_scheduler_outbox_consume_attempts.sql`，但 ORM `scheduler_outbox.py:20` 已强依赖该列。
6. **P1-CI-06：供应链门禁扫描对象不完整。** `ci.yml:300` 的 `pip-audit` 导出漏 `--all-packages`，实测 98 包而完整 workspace 为 141 包；`docker-build.yml:96-158` 预扫描候选后又第三次构建并先推正式标签，最终 digest 才后置扫描。当前完整依赖审计未发现已知漏洞，但门禁设计仍会漏未来问题。

## 6. P2 中风险与质量问题

- `StreamTasks` 只按批次复检 Lease，`data_service.py:196-200` 对多个 yield 之间不复检；URI SAN 只比较 path 末段（`auth.py:718-721`），未约束 scheme/trust domain/namespace。
- Direct `report_result` 的 `XADD *` 响应丢失可生成重复事件；坏 control frame 会把原始字段写日志；poison/DLQ ACK 后不触发源 stream 裁剪；启动期间取消可遗留 client/后台任务。
- 现有 snapshot 的空 `subdir` 在重投时回退到当前项目配置（`source_bundle_dispatch_service.py:127`），同 run 执行路径仍可漂移。
- 项目删除遗漏无 TTL 的 crawl progress；注册 orphan 清理首批失败可使后续记录饥饿，并使用跨主机 wall clock 判断恢复窗口。
- `20260713_add_worker_install_key_allowed_source.sql`、20260717 recovery migration 和 `init_db.py` 仍会静默接受同名错误类型列。
- `dependency_ids` 只保存不参与调度；trigger/execute 通过“最新 run”轮询返回 ID，会串到并发 run；请求模型声明的 execute overrides 仍直接 400。
- 前端普通故障超过约 31-34 秒后永久停止自动恢复；缓冲头删为 O(maxLines)；固定 5000 行滑窗后虚拟列表无法按长度识别新日志；协议未严格校验 status/sequence/envelope。
- `RecoveryWindowError` 的 replay 迭代在 try 外；broker unsubscribe 先丢本地状态再释放 Redis，释放失败无法重试。
- Worker/Web API/Gateway 的 stop grace 与内部 drain 预算不一致；任务只有单文件 RLIMIT_FSIZE，没有工作目录或 volume 总配额。
- mTLS 部署文档使用代码无法加载的嵌套 Worker YAML；Master HA 示例引用不存在的 `*master` anchor。
- 独立 security-scan 不阻断发布；Trivy 配置 `ignore-unfixed: true`；五服务 matrix 可能部分发布。
- 严格复杂度门禁仍是假绿：当前 baseline 接纳 901 项违规，并把本轮新增/恶化项重置为存量。函数 50 行被替换成 50 条语句，TS 没有函数级 complexity/max-lines/max-depth/max-params/no-magic-number 门禁。
- 普通 mypy 对 missing imports、contracts 和 untyped defs 较宽松；严格检查新增 Gateway 模块仍有 3 个类型错误。Boundary 也没有执行 application 不依赖 infrastructure 的硬规则。
- Direct/Gateway control 协议仍重复；包 metadata 为 1.0.0，而多个模块和 Worker 上报仍为 0.1.0。
- 新增修复缺少关键竞态回归测试；loadtest 未传 `--run-loadtests`，实际 9 个场景被 deselect；没有真实浏览器 E2E；部分 integration Redis 清理块为空，测试不具备并行隔离。

## 7. 本轮验证

| 门禁 | 实际结果 |
|---|---|
| 后端 Unit 单命令，60 秒硬超时 | 约 66% 后退出 142 |
| 后端 Unit 拆分运行 | 1932 passed / 6 skipped |
| CI Worker + Scripts 原分片，60 秒 | 约 55% 后退出 142 |
| Boundary | 15 passed |
| Integration collect-only | 155 collected / 1 ImportError，失败 |
| Contracts | 86 passed / 43 errors（Redis 16379 不可达） |
| Ruff check / format | 通过，880 files formatted |
| 普通 mypy | 516 source files，0 errors |
| 严格新增模块 mypy | 3 errors |
| 复杂度门禁 | 表面通过，但基线含 901 项违规 |
| 前端 Vitest | 18 files / 87 tests passed |
| 前端 ESLint / type-check / build | 通过 |
| `git diff --check` | 通过 |

多代理定向测试还覆盖了 Gateway、Direct、SSE、安全、部署和数据事务路径，均未出现现有断言失败；这些通过结果不覆盖本报告中的竞态、响应丢失、时钟偏差、资源放大和真实部署链路。

## 8. 验收判定

- 修复文档“P1 全部修复”“可代码修复的 P2 全部修复”“前端/CI 全绿”的结论不成立。
- 当前版本不能签署生产可用，也不应进入压测；压测会掩盖已知正确性和 CI 基线问题。
- 测试机验收应在 P1 代码缺陷、Integration 收集失败、60 秒 CI 分片和 fresh E2E 初始化问题修复后重新开始。
