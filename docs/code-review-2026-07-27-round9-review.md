# AntCode 第九轮增量复审报告（非 K8s，2026-07-27）

> **2026-07-27 最终复核更新**：本报告记录的是修复前发现。2 个 P0、14 个 P1、
> 7 个 P2 的代码修复状态、继续复核新增遗漏、精确测试结果和剩余生产验收边界，
> 统一以 `docs/code-review-2026-07-27-round9-fixes.md` 为准。当前 Ruff、format、
> mypy、严格复杂度、前端四项、transport contracts 和真实 Redis integration 均已
> 恢复绿色；真实 PostgreSQL/Sentinel/Cluster、npm audit、SSE 重启耐久和发布提交
> 闭包仍未验证，因此仍不批准生产发布或生产压测。

> 审查对象：`5380f25` 与 2026-07-27 13:30 前后的当前未提交工作树。
>
> 基线：`docs/code-review-2026-07-27-round7-review.md`、`docs/code-review-2026-07-27-round8-review.md`。
>
> 本轮性质：多代理并行复审、导入冒烟和定向测试；主审只更新报告。审查期间并发修复代理仍在修改业务代码，以下结论按 13:30 后稳定快照重新判定。

## 1. 范围与结论

项目已明确不使用 Kubernetes。本轮完全排除 `infra/k8s/`、Kustomize、NetworkPolicy、K8s Secret、K8s 探针和存储，不把相关内容计入问题或关闭标准。

本轮重点复核了最新 Direct HTTP 控制面改造、Master/Worker 消息耐久性、Lease 切代、Gateway 重连与订阅、Worker 注册恢复、Redis PEL 背压，以及非 K8s Compose 的备份和容器权限。

本轮新增或按最新代码重新确认（不是全项目累计总数；round7/round8 中未关闭且本轮未重复抄录的问题仍然有效）：

- 2 个 P0；
- 14 个 P1；
- 7 个 P2。

当前版本 **拒绝生产发布、拒绝压测**。最新 Direct 控制面修复已恢复 Worker 导入和相关单元测试，但接管日志仍会确定性丢失，结果处理仍可让旧代际覆盖新代际；结果耐久性、SpiderData fencing、Gateway 假健康、重试并发、注册恢复，以及红色 complexity、mypy、Ruff format、npm audit 门禁等 P1 也未关闭。

## 2. Round8 Direct P0 状态校正

最新工作树已把 Direct Lease grant/revoke 和 run ownership claim/renew/release 迁到 Web API：`workers_direct_control.py:101-238`。Worker ACL 对自身 Lease 只保留 `HGET/EXISTS/PTTL` 和 revoked set 的 `SISMEMBER`，并删除 `run:owner:*` 权限：`redis_acl_policy.py:78-105`。

因此 round8 的以下结论需要按当前源码重新判定：

| Round8 问题 | 当前状态 |
|---|---|
| P0-R8-01 Worker ACL 无法 grant Lease | 根因已从架构上移除：Worker 不再直接 grant；四服务 import 已恢复，但真实 Redis + HTTP 控制面闭环尚未验证。 |
| P0-R8-02 被撤销 Worker 可直接伪造 Lease 复活 | Lease/ownership 权威写权限已从 Worker ACL 删除，当前静态 ACL/Direct/ownership 定向测试已通过；真实 Redis ACL 攻击拒绝和撤销闭环仍未执行，暂不能作为生产验收关闭。 |
| P0-R8-03 Direct takeover 日志丢失 | L2 claim 已在返回成功前绑定 PostgreSQL，修复了“L2 先执行、后绑定”的方向；L1 已确认 backlog 在 L2 rebind 后才被 Master 消费的反向交错仍会永久进入 DLQ。 |

## 3. P0 发布阻断

### P0-R9-01 Direct rebind 仍会把旧代际已确认日志永久送入 DLQ

新 claim 路径先取得 Redis ownership，再在响应成功前调用 `bind_worker_run_lease_generation()`：`workers_direct_control.py:181-222`，这关闭了 round8 记录的正向窗口。

反向窗口仍存在：L1 日志可在 Gateway/Direct 写入 ingest stream 后得到成功确认；L2 随后把 `TaskRun.lease_id` 改成新代际；Master 最后消费 L1 backlog 时，`log_ingest_integrity.py:100-120` 只接受 PostgreSQL 当前 lease，随后 `log_ingest_loop.py:225-237` 把旧代际批次写 DLQ并 ACK。DLQ 没有在 rebind 后自动重放入口。

影响：正常接管即可丢失已经向 Worker 确认成功的日志。需要给已确认 backlog 建立可验证的代际过渡协议，不能只保存一个“当前 lease”并据此否定所有在途旧代际数据。

### P0-R9-02 结果 Lease 校验与 PostgreSQL 更新之间可被切代，L1 能覆盖 L2

`TaskRunService.update_result()` 在数据库事务外调用 `_validate_result_source()`：`task_run_service.py:95-112`。其中 Redis 当前 Lease 校验通过后，`_bind_lease_generation()` 对已有 `worker_id` 只按 `run_id + worker_id` 更新 `lease_id`，不比较当前 `lease_id`，也不写或比较 `lease_gen`：`:174-193,231-268`。

确定性交错为：L1 结果通过 Redis current 检查后暂停；L2 grant、ownership claim 并用单调 `lease_gen` 把 PostgreSQL 改绑到 L2；L1 恢复后按同一 worker_id 把 `TaskRun.lease_id` 覆盖回 L1，同时保留 L2 的 `lease_gen`，随后还能提交终态。新代际可能已开始真实执行，但旧代际结果获胜，行内 `lease_id/lease_gen` 还互相矛盾。结果来源验证、代际 CAS、状态与 metadata 更新必须在同一权威事务/协议中完成，不能依赖事务外的 Redis check-then-act。

## 4. P1 正确性、恢复与可用性

### P1-R9-01 有效结果在短暂基础设施故障后会被永久 DLQ

`result_loop.py:230-256` 对 `update_result`、Lease 校验、PostgreSQL/Redis 等普通异常统一处理；`_should_dead_letter()` 在 `:397-417` 仅按 `times_delivered > 5` 判定，完全不区分坏 Proto 与瞬态依赖故障。DLQ 成功后 `:195-209,422-465` ACK 原结果。

确定性复现：让 `task_run_service.update_result()` 连续六次抛数据库连接异常，第七次恢复。当前实现会在恢复前把合法结果写 DLQ并 ACK；源任务已由 Worker 结算，TaskRun 可永久停在非终态。只有确定性解码/合同错误可进入终止型 DLQ；基础设施错误必须耐久重试，或进入有自动恢复消费者的错误队列。

### P1-R9-02 Direct SpiderData 的 Lease 检查与写入不是同一原子操作

`transport/redis/transport.py:1270-1291,1315-1359` 只在写前、写后分别调用 `_require_current_generation()`。实际 item/meta Lua 在 `spider_write_fence.py:8-60` 只检查 tombstone，不接收 Lease key 或 expected lease id；project index 的 ZADD 同样无 Lease fence。

交错为：L1 通过前置检查，L2 接管并更新 Lease，L1 随后完成 item/meta/index 写入，最后的检查才失败并返回 False。写入无法回滚，旧代际数据已对读模型可见。Lease、tombstone、item/meta/index 写入必须进入同一个原子协议；Redis Cluster 下相关 key 还需共享 hash tag。

### P1-R9-03 Gateway 重连错误使用 Worker 本机 wall clock 判定服务端 Lease

`gateway/transport.py:1391-1433` 在重连时用 `expires_at_ms <= time.time()*1000` 判过期。初始 Lease 路径却已明确禁止该做法，并在 Gateway 模式信任服务端 Redis TIME：`app/lifecycle.py:185-224`。

Worker 本机快于服务端超过 Lease TTL 时，初次启动成功，任意断连后的重连却会把仍有效的同代 Lease判过期；后续新 Lease又因代际不同触发 self-fence。应使用服务端相对 TTL/权威时间或直接信任认证后的有效性判定。

### P1-R9-04 Gateway 两条订阅永久失败时 Worker 仍为 ONLINE/ready=200

`gateway/transport.py:426-490` 的 StreamTasks/WatchControl 通用循环只记录错误、退避并重订阅，不调用 `_handle_connection_error()`，也不改变 `_connected` 或 `WorkerState`。`is_connected` 在 `:244-246` 只看 channel，readiness 又只由 transport state 驱动：`app/lifecycle.py:229-243`。

当 Lease/heartbeat RPC 正常但任务流或控制流持续 UNAVAILABLE/被拒绝时，Worker 不接任务或不接 cancel/kill，却会长期报告 ONLINE 和 HTTP 200。订阅健康必须成为独立就绪条件，并对长期不可恢复错误 fail-fast 或显式离线。

### P1-R9-05 Direct PEL reclaim 无界队列绕过 Engine 背压

`transport/redis/transport.py:138-140` 创建无 `maxsize` 的 `_reclaimed_queue`；`reclaim.py:137-165` 每轮继续 claim 并逐条 put。Engine scheduler 满时停止 `poll_task()`，但后台 reclaimer 不停止，默认每 30 秒可继续认领 10 条：`reclaim_models.py:8-23`。

大量历史 PEL、长任务或执行堵塞时，消息会从 Redis 的可恢复 PEL持续搬进本机无界内存。队列必须有与 scheduler 一致的容量预算；容量不足时消息应留在 Redis，而不是先改变 ownership 再等待本地内存消化。

### P1-R9-06 V2 注册崩溃恢复依赖再次提供一次性安装 Key

`worker_registration.py:25-28` 在当前配置和环境都没有 `worker_key` 时直接返回，甚至不会打开已有 registration intent。intent 已持久保存 install key、registration_id、recovery_secret 和请求快照：`registration_intent.py:37-49,68-88`。启动 wiring 先尝试 ACK已有凭据，再走首次注册：`app/wiring.py:265-320`。

若服务端已签发、Worker 在本地保存凭据前崩溃，而安装 Key已按一次性凭据要求从环境移除，下一次启动不会使用足以恢复的 intent，固定报“首次启动必须配置安装 Key”。恢复入口应先读取已有 intent；只有创建新 intent 才要求外部 Key。

### P1-R9-07 官方 dev Direct 一键启动缺少新控制面注册凭据

`_require_control_credentials()` 现在对所有 Gateway/Direct 模式要求 Worker API/HMAC 凭据：`app/wiring.py:267-324`；`validate_transport_config()` 也无条件要求 Direct 的 `api_base_url/api_key/secret_key`：`transport/factory.py:120-135`。但官方 dev Compose 宣称可一键启动且固定 `REDIS_ACL_ENABLED=false`，没有传 `ANTCODE_WORKER_KEY`：`docker-compose.dev.yml:220-246`。

因此 fresh dev shared-password Direct Worker 在没有预存凭据时会在启动阶段固定失败；这不是 Redis ACL 开关能回滚的旧合同。应让 dev 画像显式完成受认证控制面注册，或正式删除“一键启动/共享密码回滚”的旧声明，并用 fresh Compose 启动测试证明合同一致。remote Compose 已在本轮并发修改中切换到 backendless Gateway，不再作为该问题证据。

### P1-R9-08 手动重试不会消费已有自动 retry intent，可重复执行同一任务

远程失败会在 source run 上持久化 `next_retry_at` 并投递自动 retry intent：`scheduler_loop.py:1352-1402`。手动重试只检查 source 为终态后调用普通 `trigger_task()`：`retry_service.py:403-447`，既不锁 source，也不清 PostgreSQL `next_retry_at` 或 Redis pending。手动 run 与 `retry_loop.py:657-674` 后续创建的确定性 retry run 是两个不同 run；即使手动 run 先完成，自动 intent 仍会在任务空闲后再执行一次。手动重试必须与自动 intent 在同一事务中竞争并消费唯一重试权。

### P1-R9-09 分发后的陈旧 ORM 快照可覆盖 Worker 已持久化的结果数据

Direct 派发在 `XADD` 后还会执行 stream 裁剪才返回：`worker_dispatcher.py:1031-1058`，Worker 可在调用方恢复前立即上报。结果消费者在行锁内合并 artifacts/stdout 等 metadata：`task_run_service.py:270-337`；但 scheduler 随后仍用派发前的 `execution.result_data` 快照写回分发字段：`scheduler_loop.py:1261-1289`，上层成功分支又在 `:1144-1148` 再写一次。两次都是普通 JSON 全列替换，没有锁、版本 CAS 或数据库原子 merge，可把快速 Worker 的终态结果和产物引用永久覆盖。

### P1-R9-10 更新重试配置会用陈旧 Task 对象覆盖并发运行状态和计数

`retry.py:129-152` 先读取完整 Task，再查询/校验 User，最后修改两个字段后调用无 `update_fields` 的 `task.save()`。窗口内 `execution_status_service.py:358-392` 对 status、success_count、failure_count 或 last_run_time 的更新可能被旧 Task 快照整行写回。配置端点必须只原子更新 `retry_count/retry_delay`，并对需要保护的版本使用 CAS。

### P1-R9-11 告警配置只刷新 Web API 进程，Master 长期使用旧通道

告警配置路由直接写 `SystemConfig` 后仅调用本进程 `alert_service.reload_config()`：`alert.py:135-152,203-225`。Master 启动时只初始化一次 `alert_service`；其订阅器刷新的是另一个 `system_config_service` 缓存，不会重建告警 channel：`master/__main__.py:263-286`。因此管理员更新 webhook、邮件、限流或重试设置后，真正发送大多数运行告警的 Master 继续使用旧配置。另 `alert_service.send_alert()` 总是走 `alert_manager.send_alert()` 的 `force=True` 路径：`alert_service.py:163-207`、`alert_manager.py:139-178`，使 `auto_alert_levels` 对现有生产调用没有效果。

### P1-R9-12 Direct Redis ACL 允许任意 Worker 篡改其他 run 的 SpiderData

Spider key 以 run/project 为维度，没有 worker 维度；ACL 对 `spider:{*}:data/meta/tombstone`、`spider:index:*` 和 `spider:dedup:*` 授予共享写权限：`redis_acl_policy.py:92-101`。Redis ACL 无法表达“只写当前 Worker 被分配的 run”，而 Direct 写入又不经过可信控制面。任一失陷 Worker 可伪造或删除其他 Worker 的抓取数据、meta 和 project index。需要把 Worker 身份/Lease 纳入同 slot 的服务端原子写协议，或重新设计含 worker 归属的 key 与最终发布流程。

### P1-R9-13 Direct Worker 可跨组读取并 ACK 其他 Worker 的全局控制消息

所有 Direct Worker 对同一 `control:global` key 获得 `XGROUP CREATE/XREADGROUP/XPENDING/XCLAIM/XACK/EVAL`：`redis_acl_policy.py:84-92`。Redis ACL 不限制 group/consumer 参数，攻击者可使用另一个 Worker 的可预测 group 名读取、claim 或 ACK cancel/kill 等控制消息；还可创建孤儿 group 阻碍安全裁剪。现有 owned-ACK Lua只保护守规矩客户端，不能阻止直接发命令。global group 应由可信控制面创建和消费，Worker 权限必须限制到无法跨组操作的协议边界。

### P1-R9-14 当前 CI 质量与依赖安全门禁直接失败

按 CI 原命令复跑存在四类稳定红灯：

- `make complexity`：**11 个 NEW、7 个 WORSE**。生产代码包括 `run_ownership_rpc.py` 307 行、`direct_control.py::_response_data` 新魔法数、`lease_service.py` 759 -> 766 行、`gateway/server.py` 484 -> 485 行、`workers.py` 613 -> 617 行、`transport/factory.py` 572 -> 615 行，以及 `RedisTransport.__init__` 位置参数 6 -> 7；`logs.test.ts` 和多个测试文件也新增/恶化。
- `uv run mypy packages services --ignore-missing-imports`：**101 errors in 24 files，checked 591 source files**，其中包括本轮新增的 `sse_event_stream.py`、Worker transport factory 等生产路径错误。
- `uv run ruff format --check .`：`workers_crud.py`、`workers_direct_control.py`、`transport/factory.py` 三个文件未格式化；普通 `ruff check` 通过不能抵消 format job 失败。
- `npm audit --audit-level=high`：全 lockfile 为 **15 high / 0 moderate** 并返回 1；除 ESLint/minimatch/brace-expansion/PostCSS 工具链外，生产依赖 `react-router-dom@7.18.1` 也被当前公告命中。`--omit=dev` 后仍有 **2 high**，均来自同一条 React Router RSC-mode CSRF 公告。当前前端是 SPA，实际可利用性需结合未启用 RSC action 的事实复核，但 CI 原命令和生产依赖审计都确定不是绿灯。

这不是要求一次性消灭全部历史基线；complexity 报告的是本次相对基线新增/恶化，mypy、format 和 npm audit 又是 CI 明确阻断项。不能通过扩大 baseline、关闭检查或忽略 audit 退出码制造绿灯。同期 Bandit 无 HIGH，Python `pip-audit` 扫描 136 个锁定包为 0 漏洞，说明应精确修复红灯而不是弱化安全门禁。

## 5. P2 合同与运维问题

### P2-R9-01 Direct 与 Gateway SpiderData 输入合同分叉

spool 校验允许 `sequence=0`：`engine/spider_spool.py:92-99`，数据模型默认也是 0：`plugins/spider/data/models.py:20-49`；Gateway 在 `handlers/spider_data.py:251-270` 要求正整数并限制 batch count、batch bytes、item bytes 和文本长度。Direct parent transport没有复用 `SpiderIngestLimits`，现有测试甚至要求单次 10001 items 成功：`test_spider_parent_transport.py:39-68`。

同一任务在 Direct 成功、Gateway 失败，且 Direct 可构造超大 Lua argv形成内存/CPU放大。两条 transport 必须复用同一规范化和上限合同。

### P2-R9-02 本地备份无 retention，失败会留下伪完整文件

`docker-compose.prod.local-backup.yml:18-23` 每天新增 custom dump，没有清理策略；`pg_dump` 直接写最终 `.dump` 文件，进程中断会留下看似可用的半成品。应先写 `.partial`，校验成功后原子改名，并实现文档声明的保留期。该本地卷仍不是异机/对象存储灾备，round7 的备份恢复发布条件不能视为已关闭。

### P2-R9-03 Gateway 容器仍可改写自身代码和虚拟环境

生产 `docker-compose.prod.yml:145-179` 未给 Gateway 设置 `read_only`。`Dockerfile.gateway:82-99` 又把应用代码和 `.venv` 所有权交给运行用户。服务失陷后可持久化篡改代码并在容器重启时继续执行。应使用只读根，仅给 `/app/data` 和必要 tmpfs 开放写权限。

### P2-R9-04 重试统计把一次自动重试重复计数，手动重试反而不可见

自动重试既把 source run 的 `retry_count` 从 n 改为 n+1，又让新 retry run 继承 n+1：`scheduler_loop.py:1034-1047,1369-1402`。统计却对所有 run 求 `sum(retry_count)` 并按 `retry_count > 0` 计“重试执行”：`retry_service.py:567-588`，一次自动重试即可报告两次；多代误差继续放大。手动重试创建普通 run，`retry_count=0` 且没有 source link，又完全不进入统计与历史。重试关系需要独立、唯一的 source/retry 事件模型，不能把累计次数复制到两行后再求和。

### P2-R9-05 取消待重试会覆盖原失败诊断但保留矛盾 intent metadata

`retry.py:210-229` 对 FAILED/TIMEOUT 等终态 source 清 `next_retry_at` 时，把原 Worker `error_message` 覆盖为“重试已取消”，却没有清 `result_data.retry_intent`。用户失去原始失败原因，行内又同时显示“没有待重试时间”和“存在 retry intent”。取消应保存独立审计事件，并原子清理 intent 字段，不应破坏历史执行诊断。

### P2-R9-06 已下调或禁用的重试配置不约束存量 intent

配置端点允许把 `retry_count` 调到 0：`retry.py:119-152`，但 `retry_intent_guard.py:48-58` 只校验 source 行的旧 intent/count；创建 retry run 时虽已锁当前 Task，却不比较 intent count 与当前配置：`scheduler_loop.py:997-1048`。因此管理员禁用重试后，既有 intent 仍会创建新 run。暂停任务则在 `:1003-1005` 返回 busy，`retry_loop.py:586-593` 每 30 秒无限重排。配置变更应显式定义并原子实施对存量 intent 的取消或保留策略。

### P2-R9-07 SSE Redis 恢复协议没有进入真实 Redis 自动化门禁

SSE 事件 Lua 已在 Redis 8 手工故障注入中通过负账本、future cursor、100 并发和总账一致性验证，26 个 SSE/follower 定向测试也通过；但 `test_sse_event_stream.py:17-22` 主要搜索 Lua 源字符串，仓库自动测试没有在真实 Redis 上执行命令顺序、四账本恢复、XTRIM ID 单调性、并发原子性、响应丢失重投或 AOF/重启一致性。当前修复缺少可重复的回归门禁，后续 Lua 顺序或账本逻辑退化仍可能在全绿测试中进入生产。应使用必需的真实 Redis fixture 覆盖这些故障注入，不能把手工验证当作长期合同。

## 6. 验证结果与测试盲区

本轮实际执行结果：

| 检查 | 结果 |
|---|---|
| 四服务 import 冒烟 | 最终全部通过；本轮曾发现 Worker 运行时注解 `NameError`，并发修复改为字符串注解后复跑关闭 |
| ResultLoop / retry 定向测试 | 26 passed；未覆盖“连续六次基础设施异常后恢复”、手动/自动 intent 竞争、陈旧 ORM 写覆盖或配置整行覆盖 |
| Gateway ownership + Worker connection/stream | 35 passed |
| Worker SpiderData + Redis transport/PEL recovery | 55 passed |
| Worker persistent credentials | 26 passed；未覆盖“无外部 install key，仅依赖已有 intent 恢复” |
| 生产 Compose 合同 | 11 passed；没有验证 fresh Worker bootstrap、备份 retention/原子文件或 Gateway 只读根 |
| ACL + Direct control + ownership | 163 passed；真实 Redis ACL live 用例因未配置 fixture 为 1 skipped |
| Web API Direct control/ACL lifecycle | 11 passed |
| SSE / follower | 26 passed；Redis 8 手工故障注入通过，但真实 Redis 自动化门禁缺失 |
| 前端 | type-check、零警告 lint、production build 通过；25 files / 145 tests passed |
| Ruff / import | `ruff check` 与四服务 import 通过；`ruff format --check` 有 3 个文件失败 |
| 严格复杂度门禁 | failed：11 NEW、7 WORSE；同时有 4 IMPROVED、7 RESOLVED，改善项不能抵消新增回退 |
| mypy | failed：101 errors in 24 files，checked 591 source files |
| 依赖安全 | Bandit 无 HIGH；Python pip-audit 136 包 0 漏洞；npm audit 15 high / 0 moderate，生产依赖仍有 2 high |
| 全量 unit（一次快照） | 2257 passed, 18 failed, 6 skipped；其中 16 条因当前沙箱禁止本地 TCP/Unix socket，remote Compose 与 npm registry 来源合同随后已修正；来源合同定向复跑 12 passed，修正后未再跑全量 |

当前工作树约有 760 个变更条目，其中约 452 个未跟踪文件。round7 的“发布提交闭包不完整”仍成立；任何只在当前脏工作树得到的测试结果都不能证明 fresh checkout 或发布镜像包含这些修复。

## 7. 关闭标准

1. Worker import/start 冒烟进入 CI，并证明 Gateway、Direct ACL、Direct shared-password 三种声明模式的真实启动合同一致。
2. Direct 控制面在真实 PostgreSQL + Redis 7 上完成 grant、renew、revoke、ownership claim/bind/renew/release、日志、结果和接管闭环；Worker 原始 Redis ACL不能改写 Lease 或 ownership。
3. L1/L2 两个方向强制插入 rebind：已确认旧 backlog 与新代际首条数据都只持久化一次，不进 DLQ、不丢失。
4. 结果消费者只对确定性坏消息终止；数据库、Redis 和事件发布故障恢复后必须处理同一权威结果。
5. 结果 Lease 校验、单调代际 CAS、状态和 metadata 在同一权威协议中完成；L1 不得在 L2 rebind 后覆盖 lease 或提交终态。
6. SpiderData 的身份、Lease、tombstone 和写入使用同一原子 fence；Direct Worker 不能写其他 run，Direct/Gateway 共享尺寸、字段和 sequence 合同。
7. Gateway 订阅故障、时钟偏移和 Direct PEL 满载均有故障注入测试，readiness 与实际接单/控制能力一致；global control 不允许跨 Worker group 消费或 ACK。
8. 手动/自动重试共享唯一 intent，分发与结果 metadata 不发生陈旧覆盖，配置写入不覆盖运行状态；告警配置变更传播到全部 Master。
9. 注册 intent 可在不再次提供安装 Key 的进程重启中恢复；所有声明的 Direct 配置模式均通过 fresh 配置和启动测试。
10. complexity 恢复为零 NEW/WORSE，mypy、Ruff format、npm audit 全部通过；不得用扩大 baseline、关闭检查或忽略退出码掩盖回退。
11. 非 K8s 生产验收使用 TLS bootstrap、只读容器、可验证的异机备份和恢复演练；全部 P0/P1 关闭前不进行压测。

## 8. 最终判定

1. 当前版本：**拒绝生产发布**。
2. 当前版本：**拒绝压测**。
3. K8s：**完全排除，不参与任何问题、测试或关闭条件**。
4. round8 Direct ACL 前两项已出现正确修复方向，但 2 个 fencing/数据丢失 P0、14 个 P1、7 个 P2 和发布提交闭包仍未关闭；complexity、mypy、Ruff format 与 npm audit 门禁仍为红色。
