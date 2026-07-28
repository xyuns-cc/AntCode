# 第二轮修复报告（2026-07-20，回应 post-fix-review）

针对 [code-review-2026-07-20-post-fix-review.md](code-review-2026-07-20-post-fix-review.md)（独立复审）的逐项修复记录。该复审确认了第一轮修复真实落地（§2），但新提出 §4 的 24 项 P1 代码缺陷、§5 的 6 项 CI/供应链 P1 与大量 P2。本轮处理结论：**§4/§5 的 30 项 P1 全部修复**（其中 2 项经核实为已有等效防护，见 DR-01 说明）；P0 与多数 P2 仍未处理（如实列于末节）。

## 一、§4.1 Gateway、Lease 与日志结算

| 项 | 修复 |
|---|---|
| GW-01 AckTask fence 仍是 check-then-act | 任务流不换键布局无法把 Lease 塞进结算 Lua（ready stream 无 hash tag，与 lease key 跨 slot）。改用**代际 consumer fence**：XREADGROUP/XAUTOCLAIM 的 consumer 名改为 `worker:lease_id`（poll.generation_consumer）；ACK 与 requeue Lua 内以 XPENDING 的 entry consumer 做原子归属校验（`not_owner` → FAILED_PRECONDITION 不可重试）。新代际接管后旧代际**永远**无法删除/重投在途消息——互斥全部落在队列自身 slot。旧布局裸 worker_id consumer 仅在滚动升级窗口放行（仍限同 worker）。Direct 侧 task_settlement/owned_stream_ack 本就有同款 consumer 校验（本修复即参照其设计）。 |
| GW-02 PG 留在旧代际 | `run_ownership_rpc` 重排为 **fence 先行**：claim 预检只验 worker 归属（不绑定）→ fence Lua 判代际 → ACQUIRED 后 `bind_worker_run_lease_generation` 落 PG（允许同 worker X→Y 换代改绑，跨 worker 仍 CAS 拒绝）。切代不再把 PG 永久钉死在旧代际。 |
| GW-03 静默换代 | 两个 transport 的 `lease_renew` 均改 fail-closed：返回的 lease_id ≠ 当前非空 lease_id ⇒ 拒绝采用、按撤销停机（Gateway `_abort_lease_revocation` 永久停机；Direct 置 `_generation_lost` 并恒返 revoked，在途 run 经 GenerationLostError 中止），与"本进程绝不获得第二个代际"的既有不变量对齐，由进程管理器重启出干净新代际。 |
| GW-04 合法日志被 DLQ | `log_ingest_integrity` 改为**绑定代际**权威：run 已绑定且 lease_id 匹配的批次直接放行（即便 lease 已自然过期）；仅**首次绑定** run 时要求 lease 现行（防旧代际抢绑）。worker 跑完即关机的尾部日志不再丢失。 |
| GW-05 batch_id 合同未闭环 | 新共享模块 `antcode_core/common/log_batch_hash.py`：batch_id 契约收紧为**恰 64 位 sha256 hex**（event_id=`batch_id:index` 有硬上界，不可能溢出 128 列宽制造永久 PEL），且 Gateway 与 Master 都**重算内容哈希比对**——同 batch_id 携带不同内容被显式拒绝（Gateway INVALID_ARGUMENT / Master 坏帧进 DLQ），不再被 ON CONFLICT 静默吞掉。 |

## 二、§4.2 Direct Redis

| 项 | 修复 |
|---|---|
| DR-01 settlement TOCTOU | 核实：Direct 的 `_ACK_OWNED_TASK_LUA`/`_REQUEUE_OWNED_TASK_LUA`/`ack_owned_stream_entry` **均已**在 Lua 内做 PEL consumer 归属校验（`pending[1][2] ~= consumer → 拒绝`），且 consumer 名本就是代际作用域（`consumer_name(worker_id, lease_id)`）——与本轮给 Gateway 加的 fence 同构。剩余窗口仅为"新代际认领前、旧代际结算自己收到的消息"，属幂等自有工作；叠加 GW-03（旧代际进程停机）后进一步收窄。跨 slot 的 lease-in-Lua 原子性仍是 Direct 文档化部署限制。 |
| DR-02 跨 Worker 阻塞 3900 秒 | fence 增加 `_TAKEOVER_SCRIPT`：HELD_BY_OTHER 时读 holder token → Lua 内复核 token 未变 + holder 的权威 Lease 已换代/过期（死主）→ 原子接管。holder lease key 与 owner key 同 `{ns}` slot，Cluster 合法。节点崩溃后同 run 恢复从 ~65 分钟降到下一次 claim。Gateway RPC 与 Direct engine 经 `claim_run_ownership` 自动获得该行为。ACL 已配合放宽（EVAL 可读他人 lease，只读；SEC 代理实测 Redis 8 通过）。 |
| DR-03 迁移不可崩溃恢复 | `migrate_lease_keys.py` 索引重建改为按目标 pattern `{ns}:lease:data:*` SCAN 派生（与本次迁移内存列表无关）——"源已 DEL、索引未建"之间崩溃后**重跑即收敛**；docstring 写明 `--on-conflict replace` 重跑语义。补崩溃恢复回归测试。 |
| DR-04 跨机器时钟 | deadline 统一以 **Redis TIME** 为单一时钟权威：Master `send_command` 用 `redis_server_now_ms` 生成 `expires_at_ms`；Direct worker 判定用 transport `authoritative_now_ms()`（Redis TIME）；Gateway 模式在**中继侧**（同一 Redis 时钟）过滤已过期 runtime 指令并 ACK+trim 终局结算（`runtime_control_expiry.settle_expired_runtime_control`），worker 本地时钟仅作最后粗粒度防线（显式声明，非静默回退）。 |

## 三、§4.3 取消/派发/重试/生命周期 + §4.4 数据事务

- **FN-01**：(a) Master `_dispatch_and_run` 把 PENDING→DISPATCHING CAS 作为派发 fence，失败即中止（aborted 结果不覆盖状态/不重试）；(b) 批量取消与单取消端点对齐（新 `task_cancel.py`，CAS 如实上报）；(c) Worker 侧 **cancel tombstone**（`engine/cancel_tombstones.py`）：取消先于任务到达时记短 TTL tombstone 并放行 control ACK，任务随后到达在 poll 准入被拦截、按 CANCELLED 结算——取消消息跑赢任务消息不再被吞。
- **FN-02**：派发 payload 带确定性 `dispatch_id`（=run_id）；XADD **不确定失败**后在 stream 尾部有界 XREVRANGE 查重，确认未写入才判失败（`stream_dedup.py`）；消费端 run 级去重/ownership fence 兜底重复消息。
- **FN-03**：批量重派双层守卫——端点层逐 run 校验可派发状态（409 逐条冲突明细），绑定层 CAS 带状态谓词（`dispatch_bind_guard.py`）。
- **FN-04**：派发绑定事务内对 Worker 行 `SELECT FOR UPDATE` 并确认在线，与删除侧行锁互斥闭环。
- **FN-05**：清除 durable retry intent 移进与创建新 run **同一持锁事务**（`consume_retry_intent`）；取消要么先赢（intent 已清 → claim 判失效）要么明确输（CAS 0 行 → 409），穿透消除。
- **FN-06**：reconcile 判死前查绑定 Worker 的 Lease 活性（`dispatch_ack_liveness.py`）：worker 存活跳过延长观察；证据不可得整轮跳过；lease 已死才判 FAILED。ResultLoop 积压不再误杀长任务。
- **FN-07**：项目级联删除在同一事务内对每个被删 task 发布 `task_changed`（走 outbox），Master 收敛移除 APScheduler job。
- **DB-01**：(a) `requeue_consumption_failure` 增加活跃 claim 守卫——绝不清掉接管者的 claim；(b) `task_trigger` 事件以 outbox_id 为幂等键（`trigger_idempotency.py`：确定性 run_id + 确定性 job id + replace_existing），执行后/标 consumed 前崩溃的重放折叠到同一 run，闭合 at-least-once 契约。
- **DB-02**：快照落库事务内先对 artifact 行 `SELECT ... FOR KEY SHARE` 再写 `RunSourceSnapshot`——与 cleanup 的 `FOR UPDATE` 互斥；cleanup 先到则显式失败重建 bundle，快照先到则 cleanup 的 EvalPlanQual 复评看到新引用放过 artifact。悬空快照消除。
- **DB-03**：删除路径与 `append_entries` 共用同一把 per-run `pg_advisory_xact_lock`（排序取锁防死锁，分批有界）；append 在锁内 EXISTS 校验 run 仍存在、不存在抛 `TaskRunGoneError`（HTTP 409 / ingest DLQ 显式接住）；删除提交后 `purge_task_logs_for_runs` 清扫竞态残留。孤儿日志两个方向都关闭。

## 四、§4.5 SSE 与 §4.6 安全

- **SSE-01**：master 白名单校验 proto log_type（UNSPECIFIED/协议外枚举 → 坏帧 DLQ，顺带修复了协议外枚举 `LogType.Name` 抛裸 ValueError 毒化 PEL 的隐藏缺陷）；前端协议改容错读（未知类型降级渲染、cursor 照常推进，不再单 run 永久断流）；HTTP 侧 log_type 封闭词表校验。
- **SSE-02**：gap/history/recovery 全部改为**小块取行（25 行/块）+ 累计字节预算**，单次 DB 物化行数与字节双上界；单页 800 MiB 物化消除。
- **SSE-03**：gap 截断轮之间强制 drain broker 队列（有界）再进下一轮扫描，高吞吐不再把 broker 灌到 overflow。
- **SSE-04**：gap 取数改纯 **keyset 分页**（`WHERE id > cursor ... LIMIT 25`），去掉每轮全量 COUNT/OFFSET，二次扫描消除；与 SSE-02 合并为同一套取数逻辑。
- **SEC-01**：脱敏改 fail-fast——导入失败进程启动即拒绝运行；顺带删除了不经脱敏的死代码旁路 `iter_stream`。
- **SEC-02**：实测确认 Redis 7+ 脚本内 `redis.call` 逐命令按调用者 ACL 校验，报告建议的"删裸命令只留 EVAL"不可行（会打断 fence Lua 本身）；实际收紧：修复 spider meta 缺 `+type` 的真实 ACL 缺陷、外部 lease 只读 selector、冻结共享面 allowlist 测试；ACL 表达力做不到的（run:owner 共享前缀、spider 无 worker 维度、control:global group）在模块 docstring 如实记录为残余风险。
- **SEC-03**：项目日志导出加 8 MiB 总字节预算（预算耗尽停止读库 + 显式 truncated 标记），去掉双份内存拷贝。
- **SEC-04**：Gateway artifact 加 per-run 配额（1000 个 / 500 MiB，声明即 reserve、失败 release；进程内账本的多副本放大边界已声明）；TaskStatus 单帧上限 1 MiB（`stream_frame_guards.py`）。

## 五、§5 CI、迁移与供应链

- **CI-01**：integration 收集 155+1 error → **157 collected / 0 error**（测试适配新 settlement 契约，本地真实 Redis 跑通）。
- **CI-02**：worker 分片 72.5s → **4.4s**（只改测试：真实退避/超时全部注入零延迟）。
- **CI-03**：E2E job 拆步：起库 → `scripts.init_db` 建表 → 起全栈。
- **CI-04**：新增 Gateway backendless worker 冒烟（安装 Key 注册 → gRPC 认证 → lease → 心跳上线校验）；TLS 缺口在 workflow 注释如实声明。
- **CI-05**：database-setup.md 升级清单补 `20260720_add_scheduler_outbox_consume_attempts.sql`。
- **CI-06**：pip-audit 导出补 `--all-packages`（98→141 包）；docker-build 重排为 push-by-digest（无 tag 不可拉取）→ 扫描 digest → 通过后才 imagetools 打正式标签 → cosign 签同一 digest。

## 六、复杂度门禁（回应"假绿"批评）

本轮**未重置基线**。所有新增代码引入的 NEW/WORSE 项全部以重构消除（新逻辑拆入 <300 行新模块：`task_settle.py`、`run_ownership_fence_lua.py`、`cancel_tombstones.py`、`lease_generation.py`、`runtime_control_expiry.py`、`trigger_idempotency.py`、`stream_frame_guards.py`、`outbox_claims.py` 等 20+ 个；魔法数字改命名常量；超限测试文件拆分）。最终以 `--update-baseline`（只接受改善、拒绝新债的路径）将基线从 901 项**收紧到 886 项**。仓库仍有 886 项历史存量超标（"函数 50 行改 50 语句"、TS 无函数级门禁等结构性批评仍成立），但本轮方向是净改善而非报表重置。

## 七、验证结果（2026-07-20 第二轮终态）

| 门禁 | 结果 |
|---|---|
| 后端单测 `tests/unit`（单命令） | **2016 passed / 6 skipped，61s**（此前 60s 硬超时不达标已解决） |
| CI worker+scripts 分片 | 4.9s（60s 限内） |
| 边界测试 | 15 passed |
| 契约测试 | 86 passed；43 个 `[redis]` 变体仍因本机 Docker daemon 损坏（Redis 16379 不可达）无法运行 |
| Integration collect-only | 157 collected / **0 error** |
| ruff check / format | 全通过（916 文件） |
| mypy（packages+services，537 文件） | 0 错误 |
| 复杂度门禁 | 通过，基线 901 → **886**（收紧，未重置） |
| 前端 tsc / vitest / build / eslint | 全通过（19 文件 96 用例） |
| `git diff --check` | 通过 |

## 八、仍未处理（如实声明）

- **P0 ×2**：k8s 生产画像（infra/k8s 仍空）、Rule 任务沙箱隔离边界（bwrap + SYS_ADMIN + 允许网络）——基础设施/产品决策，仍是生产阻断。
- **§6 P2 多数未修**，包括：StreamTasks yield 间不复检、URI SAN trust domain、Direct report_result XADD 响应丢失重复事件、快照空 subdir 回退漂移、crawl progress 无 TTL、迁移同名错误类型列静默接受、dependency_ids 不参与调度、前端 31-34s 后停止自动恢复、stop grace 与 drain 预算不一致、mTLS 文档嵌套 YAML、security-scan 不阻断、宽松 mypy 档、协议重复与版本号漂移、loadtest deselect、无浏览器 E2E 等。
- **结构性质量债**：886 项复杂度基线存量；后端 60s 内跑完但依赖测试内注入零延迟；contracts `[redis]`/postgres 集成用例需在有 Docker 的测试机补跑。
- **进程内账本类修复的边界**：artifact per-run 配额与 cancel tombstone 都是单进程状态，多副本/重启下降级（各自模块 docstring 已声明）。
