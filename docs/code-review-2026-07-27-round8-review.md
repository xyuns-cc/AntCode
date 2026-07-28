# AntCode 第八轮最终增量复审报告（非 K8s，2026-07-27）

> 审查对象：`5380f25` 与 2026-07-27 13:28 左右的未提交工作树。
>
> 基线：`docs/code-review-2026-07-27-round7-review.md`。本文件已吸收后续并行复审草稿，不再以审查中途的源码快照作为开放结论。

## 1. 范围与结论

项目已明确不使用 Kubernetes。本轮完全排除 `infra/k8s/`、Kustomize、NetworkPolicy、K8s Secret、K8s 探针与存储；非 K8s 生产面包括 Docker/Compose、Gateway/Direct、Master HA、Web API 多进程、PostgreSQL、Redis、TLS/mTLS 与浏览器前端。

本报告是 round7 的增量，不重复抄录其中仍开放的问题。本轮最终确认：

- 2 个新增/升级 P0；
- 65 个 P1 问题组；
- 32 个 P2 问题组。

连同 round7 的发布提交闭包，当前至少有 3 个 P0 发布阻断。结论是 **拒绝生产发布、拒绝压测**。测试绿灯不能抵消已由代码交错证明的数据丢失、旧代际覆盖与状态机错误。

## 2. 基线校正

| 既有结论 | 当前状态 |
|---|---|
| 原 P0-R8-01 Worker 无法 grant Lease | 已关闭。grant/revoke 已迁入 HMAC/API Key 认证的 Web API 控制面，Worker 不再直接执行 sequence `INCR`。 |
| 原 P0-R8-02 Worker 可伪造 Lease/ownership | Lease 与 ownership 权威写面已从 Worker ACL 删除；静态问题已关闭。round7 P1-SEC-05 中 SpiderData 与 global control 的共享 ACL 风险仍开放。 |
| Round7 P1-DIST-03 结果可反向覆盖新 Lease | 仍成立且影响达到发布阻断，本轮升级为 P0-R8-04。 |
| Round7 P1-SSE-04 全局 SSE 流无字节高水位 | 已关闭。`sse_event_stream.py:13-75` 以 Lua 原子维护 64 MiB/20,000 条双上限及一致性账本。 |
| 已关闭：P1-R8-DEPLOY-09 手动未合并分支发布 | 发布 workflow 仅允许 `workflow_call`，调用方限制为 main 或版本 tag 的 push。 |
| 已关闭：P2-R8-22 短 SHA 标签 | 发布标签已使用完整 `${{ github.sha }}`；arm64 功能测试与 Docker 依赖更新仍开放。 |
| Direct shared-password 回滚路径 | 已从 Worker 启动路径删除；dev 改为独立 ACL，remote 改为 Gateway backendless。`.env.example` 的旧回滚说明仍需同步，但不再构成 Lease 写旁路。 |

## 3. P0 发布阻断

### P0-R8-03 已确认旧代际日志 backlog 在 takeover 后永久进入 DLQ

Direct/Gateway claim 已在返回成功前完成 Redis fence 与 PostgreSQL `TaskRun.lease_id/lease_gen` 绑定，关闭了“L2 先执行、PG 后改绑”的正向窗口。但反向交错仍存在：

1. L1 日志通过入口的当前 Lease/ownership 校验并 XADD，生产者收到成功；
2. Master 尚未消费时，L2 takeover 把 PostgreSQL 当前 Lease 改为 L2；
3. Master 消费 L1 backlog，`log_ingest_integrity.py:100-138` 只接受当前 `TaskRun.lease_id`；
4. `log_ingest_loop.py:225-249` 将批次写 DLQ并 ACK 原消息。

入口确认发生在 rebind 前，拒绝发生在 rebind 后，DLQ又没有按 run 历史合法代际自动重放。正常接管即可永久丢失已确认日志。必须持久化可审计的 run 代际历史/接受水位，或建立能证明旧、新代际边界的落库协议，不能只比较一个可覆盖的当前 Lease。

### P0-R8-04 结果 Lease 校验与 PostgreSQL 更新可被切代，L1 能覆盖 L2

`TaskRunService.update_result()` 在事务外执行 `_validate_result_source()`：`task_run_service.py:95-112`。Redis 当前 Lease 校验通过后，`_bind_lease_generation()` 只按 `run_id + worker_id` 更新 `lease_id`，不比较当前 `lease_id`，也不写/比较 `lease_gen`：`:174-193,231-268`。

确定性交错：L1 结果通过 Redis 校验后暂停；L2 grant/claim 并以更高 `lease_gen` 改绑 PostgreSQL；L1 恢复后把 `lease_id` 覆盖回 L1但保留 L2 的 `lease_gen`，随后仍可提交终态。旧代际因此可战胜已开始执行的新代际，行内 Lease ID 与 generation 还互相矛盾。结果来源验证、单调代际 CAS、状态与 metadata 必须进入同一权威事务/协议。

## 4. P1 分布式正确性、恢复与 Worker

| ID | 问题与证据 |
|---|---|
| P1-R8-DIST-01 | standby Master 会消费并完成 leader-only `task_trigger/task_changed`，但本地 scheduler 未启动，随后仍 ACK/完成 outbox：`scheduler_event_loop.py:108-123,323-393`。 |
| P1-R8-DIST-02 | Scheduler 与 ResultLoop 仅在无新消息时读 PEL，持续流量会饿死失败项；`XAUTOCLAIM` 返回值又未合入本轮 messages：`scheduler_event_loop.py:117-167`、`result_loop.py:130-193`。 |
| P1-R8-DIST-03 | no-ACK reconcile 固定前 200 条、retry 恢复固定前 500 条，无稳定排序/游标；活项或未来 intent 可永久遮挡尾部故障：`dispatch_ack_liveness.py:73-123`、`retry_loop.py:688-708`。 |
| P1-R8-DIST-04 | 生产 Master 使用的 Scheduler 不接 redispatch；派发异常/超时也不创建同一 retry intent，返回失败与抛异常语义分叉：`scheduler_loop.py:923-937,1238-1350`。 |
| P1-R8-DIST-05 | 全局 scheduler 在数据库配置加载前于模块导入期构造，`max_concurrent_tasks/scheduler_timezone` 重启后仍可能使用环境旧值：`master/__main__.py:36-42,276-284`。 |
| P1-R8-DIST-06 | 旧 outbox consumer 遇到其他活跃 owner 时把 `True` 当“已重投”，随后 XACK；新 owner 再失败时事件永久丢失：`outbox_service.py:175-195`、`scheduler_event_loop.py:274-307`。 |
| P1-R8-DIST-07 | redispatch 成功不推进 TaskRun；结果事务内业务冲突以 `False` 返回而不回滚前序写入，可留下部分 ACK/runtime/metadata：`redispatch_loop.py:125-140`、`task_run_service.py:111-172`。 |
| P1-R8-DIST-08 | 启动恢复固定 100 条且只在进程启动运行；活性只看 Worker 任意活 Lease，不看 run 代际，恢复还会先失败旧 run再污染 Task 公共参数：`task_persistence.py:155-217,339-391`。 |
| P1-R8-DIST-09 | Crawl batch start/pause/resume/cancel 缺 CAS，取消固定五轮，能力路由漏 render/runtime，非 Rule 项目可启动后空转：`batch_service.py:137-282`、`batch_dispatcher_service.py:80-175,280-302`。 |
| P1-R8-DIST-10 | 合法 `result_data` 超 2 MiB或序列化失败时静默丢 update，但 TaskRun 仍 SUCCESS、消息仍 ACK、Worker 仍结算：`task_run_service.py:29-47,270-337`。 |
| P1-R8-DIST-11 | stop/batch cancel 的 `UNASSIGNED_TASK_STATUSES` 漏 `DISPATCHING + worker_id=NULL`，可先写 CANCELLED 后仍完成绑定和真实执行：`task_cancel.py:23-29,86-95`。 |
| P1-R8-DIST-12 | ResultLoop 将数据库/Redis等瞬态异常与坏 Proto 一样按投递次数送 DLQ并 ACK；依赖恢复前合法结果可永久丢失：`result_loop.py:230-256,397-465`。 |
| P1-R8-DIST-13 | 同项目批量派发只把首个 run 写入 `RunSourceSnapshot`，其余 run复用 URI但没有授权快照，Gateway 下载固定拒绝：`worker_dispatcher.py:773-826`、`source_bundle_dispatch_service.py:21-106`。 |
| P1-R8-DIST-14 | 删除 Worker 先撤销 Redis ACL，再在数据库锁内复查活跃 run；并发派发可使删除返回 409但 Worker 凭据已失效：`worker_service.py:230-267,316-406`。 |
| P1-R8-DIST-15 | heartbeat/健康同步对可能陈旧的 Worker ORM 对象执行全行 `save()`，可覆盖并发 API Key轮换、ACL revision、维护态和资源设置：`worker_heartbeat_service.py:213-233,516-559`。 |
| P1-R8-DIST-16 | Task PATCH 在事务外加载对象并全行保存，并发局部更新可互相覆盖，陈旧请求还可重新启用已停用任务：`scheduler_service.py:311-347`。 |
| P1-R8-DIST-17 | HTTP Worker 状态上报忽略 `ExecutionStatusService=False`，不存在 run、非法状态或 CAS 冲突仍返回 `updated=true`并追加状态日志：`workers_report.py:170-189`、`distributed_log_service.py:106-133,290-309`。 |
| P1-R8-DIST-18 | Worker 负载解析用 `value or 100`，合法 CPU/内存 0 被改成满载并被路由淘汰：`worker_dispatcher.py:96-129,212-233`。 |
| P1-R8-DIST-19 | 手动 retry 不消费源 run 的自动 retry intent，同一失败可产生手动 run 与到期自动 run；inactive/busy 时还可能返回成功却不创建 run：`retry_service.py:403-447`、`retry_loop.py:657-674`。 |
| P1-R8-DIST-20 | Worker 可在 XADD 后立即上报，Scheduler 随后用派发前的陈旧 `result_data` 全列写回，覆盖 artifacts/output：`scheduler_loop.py:1144-1148,1261-1289`。 |
| P1-R8-DIST-21 | 重试配置端点只改两个字段却全行保存陈旧 Task，可覆盖并发状态、计数与时间：`retry.py:129-152`。 |
| P1-R8-DIST-22 | Direct PEL reclaimer 持续 claim 到无界 `_reclaimed_queue`，绕过 Engine 满载背压并把 Redis 可恢复状态搬入本机内存：`redis/transport.py:138-140`、`reclaim.py:137-165`。 |
| P1-R8-DIST-23 | Direct SpiderData 仅在写前/写后检查 Lease，实际 Lua只检查 tombstone；L1 可在 L2接管后完成不可回滚写入：`redis/transport.py:1270-1291,1315-1359`、`spider_write_fence.py:8-60`。 |
| P1-R8-DIST-24 | Gateway 重连用 Worker 本机 wall clock 判断服务端 Lease过期，时钟偏快会错误 self-fence：`gateway/transport.py:1391-1433`。 |
| P1-R8-DIST-25 | Gateway 的 StreamTasks/WatchControl 永久失败只重试，不改变 connected/state；Worker 不接任务/控制仍 ONLINE且 ready=200：`gateway/transport.py:426-490`、`app/lifecycle.py:229-243`。 |
| P1-R8-DIST-26 | V2 注册恢复在无外部 install key 时直接返回，不读取已持久化 registration intent；签发后本地落盘前崩溃无法自恢复：`worker_registration.py:25-28`、`registration_intent.py:37-88`。 |

## 5. P1 Gateway、Web API 与前端功能

| ID | 问题与证据 |
|---|---|
| P1-R8-GW-01 | Gateway 可 ACK 空 worker_id/非法 log_type/极值 timestamp，且 8 MiB payload 在 Master因额外 Redis字段变成 8 MiB+1后进 DLQ：`gateway/auth.py:69-87`、`handlers/logs.py:172-193`、`log_ingest_integrity.py:19-48`。 |
| P1-R8-GW-02 | stream 限流按帧计费，不按条目/字节；空帧与 8 MiB帧成本相同，可把默认预算放大到约 800 MiB/s：`rate_limit.py:233-241,368-378`。 |
| P1-R8-GW-03 | 匿名 Register 的 capabilities map 无上限，gRPC 可在认证/限流 handler前反序列化 50 MiB：`control.proto:41-47`、`rate_limit.py:320-329`。 |
| P1-R8-GW-04 | 匿名、免限流 Health Watch 是长期 stream，与业务共用 1000 stream总额，可耗尽业务通道：`auth.py:122-127`、`rate_limit.py:225-230`。 |
| P1-R8-WEB-01 | 旧密码登录在验证后暂停，改密撤销现有 session，登录恢复后仍可创建新 session；密码与会话撤销缺事务性 auth_version：`base.py:324-379`、`user_service.py:353-372`。 |
| P1-R8-WEB-02 | fresh 双 Web Worker 可各生成并缓存不同 RSA私钥，磁盘只保留最后一个，登录会随机解密失败：`login_crypto.py:76-84,126-181`。 |
| P1-R8-WEB-03 | 系统配置先 autocommit再 reload，非法值可持久化并在缓存 `clear()` 后触发异常，API仍可能报告成功：`system_config_service.py:100-182,243-269`。 |
| P1-R8-WEB-04 | 默认双 Web API worker 中只有写请求进程 reload；lifespan未启动配置 subscriber，品牌/运行配置长期随机新旧：`system_config_service.py:117-202`、`lifespan.py:228-237`。 |
| P1-R8-WEB-05 | 普通用户 hourly trend 对不存在的 `TaskRun.created_by` 过滤，固定 ORM FieldError 500：`dashboard.py:203-233`。 |
| P1-R8-WEB-06 | 监控采集异常被吞成全零并进入缓存，真实零负载与数据库/psutil故障不可区分：`system_metrics_service.py:130-231`。 |
| P1-R8-WEB-07 | super-admin 可从 reset 入口重置本人密码，绕过本人改密的旧密码校验：`users.py:340-407`。 |
| P1-R8-WEB-08 | FILE/CODE 项目复制不复制 ProjectSource，事务提交后组装响应固定 500并留下残缺副本；RULE还漏字段：`project.py:893-1008`。 |
| P1-R8-WEB-09 | 项目批删后台装饰器返回裸任务对象却声明 `BaseResponse[dict]`，前端读不到结果但后台继续，重试会并发删：`project.py:1147-1197`、`api_optimizer.py:214-279`。 |
| P1-R8-WEB-10 | REST 日志分页在加载最多 10,000 条正文/遍历 Redis后才切片，页参数不约束数据库、内存和 CPU：`task_log_service.py:119-195`、`tasks_runs.py:125-166`。 |
| P1-R8-WEB-11 | heartbeat 每 30 秒永久插历史行且无 retention；统计查询完整 `.all()` 载入时间范围：`worker_heartbeat_service.py:544-559`、`worker_stats_service.py:87-220`。 |
| P1-R8-WEB-12 | 项目导出先物化 200 条完整 execution，再应用 8 MiB预算，无法限制 ORM/DTO峰值内存：`project.py:595-664`、`project_export_executions.py:22-67`。 |
| P1-R8-WEB-13 | 输出 artifact 只在可变 JSON 保存 URI，无 run/blob FK或引用计数；cleanup只保护源码快照，可删除仍可见产物：`runs.py:256-307`、`artifact_cleanup_service.py:73-94`。 |
| P1-R8-FE-01 | 仓库扫描把 requirements/pyproject 选作 entry point并默认导入；UI又不提供 worker/runtime，导入成功项目不可执行：`source_bundle_paths.py:12,236`、`ScanImportDrawer.tsx:67-91`。 |
| P1-R8-FE-02 | PaginationConfig 与表单不按 method校验必填字段，错误配置可保存后运行期失败或静默单页：`schemas/project.py:87-111`、`RuleProjectForm.tsx:858-970`。 |
| P1-R8-FE-03 | 统一 Project PUT 接受 `code_entry_point`，却把它原样交给只含 `entry_point` 的 ProjectCode；非法 strategy及悬空数字 Worker ID也被接受/静默改写：`project_unified.py:15-65,237-245`、`unified_project_service.py:98-188`。 |

## 6. P1 数据库、授权、部署与质量门禁

| ID | 问题与证据 |
|---|---|
| P1-R8-DB-01 | Worker 凭据迁移只读旧明文字段却无条件覆盖新 hash/encrypted字段，混合版本可被清空：`migrate_worker_credentials.py:29-45`。 |
| P1-R8-DB-02 | 官方升级命令缺 `ON_ERROR_STOP`、版本 ledger、checksum和 post-check，部分 SQL失败仍可能返回成功：`docs/database-setup.md:90-108`。 |
| P1-R8-DB-03 | init只验证列/索引名称，不验证类型、约束、列序、谓词与唯一性，错误 schema也被当成功：`init_db.py:359-365,430-486`。 |
| P1-R8-DB-04 | migration test仅要求数据库名包含测试子串便执行 `DROP SCHEMA public CASCADE`，可误删真实库：`migration_support.py:14,48-74`。 |
| P1-R8-AUTHZ-01 | 撤销 Worker use权限后，fixed/specified/prefer/auto派发不复验 Task用户授权；统一 Project更新还接受内部数字 ID：`execution_resolver.py:27-156`。 |
| P1-R8-DB-05 | Task创建与 Project删除没有 FK/兼容行锁，竞态可提交孤儿 Task并使项目永久删不掉：`scheduler_service.py:164-248`、`project_service.py:79-118`。 |
| P1-R8-DB-06 | 官方升级对 `task_logs/task_executions` 等热表使用非并发 `CREATE INDEX`，可长时间阻塞写入：`20260717_add_task_logs_run_id_id_index.sql` 等。 |
| P1-R8-DEPLOY-01 | 生产 Worker bootstrap与永久凭据经 `http://web-api:8000` 明文传输，Gateway mTLS不覆盖身份注册：`docker-compose.prod.yml:195-207`、`worker_registration.py:101-145`。 |
| P1-R8-DEPLOY-02 | `rediss+sentinel` 解析丢 TLS/SSLContext，且无法表达 Sentinel与Master独立 ACL身份：`sentinel_url.py:11-80`、`redis/factory.py:169-193`。 |
| P1-R8-DEPLOY-03 | PostgreSQL URL query被丢弃，`sslmode/sslrootcert` 看似接受但应用仍明文或连接失败：`db/tortoise.py:25-51,93-107`。 |
| P1-R8-DEPLOY-04 | 运行时支持 `JWT_SECRET_FILE`，init与生产 Compose却强制 inline `JWT_SECRET`且无文件挂载：`auth.py:48-95`、`init_db.py:262-270`。 |
| P1-R8-DEPLOY-05 | Settings跨字段校验未启用 `hide_input_in_errors`，Pydantic ValidationError可回显管理员密码、密钥和连接 URL：`common/config.py:407-433`。 |
| P1-R8-DEPLOY-06 | PostgreSQL-only恢复后，`published_at != NULL / consumed_at = NULL` 的 outbox不会重发，Redis丢失即永久丢事件：`outbox_service.py:243-280`。 |
| P1-R8-DEPLOY-07 | Redis Cluster安装 Key迁移用跨 slot `RENAMENX`且 PostgreSQL先提交，固定 CROSSSLOT并留下半迁移：`migrate_worker_install_keys.py:130-169`。 |
| P1-R8-DEPLOY-08 | API接受 SSH Git，但 credential/model/镜像/known_hosts没有可执行合同，保存成功后 clone稳定失败：`git_url_security.py:145-189`、`git_transport.py:127-171`。 |
| P1-R8-DEPLOY-10 | CI E2E重建本地镜像，发布 workflow再次构建 multi-arch digest；被测对象不是最终制品：`ci.yml:137-208`、`docker-build.yml:70-158`。 |
| P1-R8-DEPLOY-11 | 旧库自动补列漏 `api_key_previous_expires_at`，官方清单又漏 credential SQL与数据迁移脚本：`init_db.py:112-129`、`database-setup.md:82-108`。 |
| P1-R8-DEPLOY-12 | Worker readiness不执行最小 bwrap冒烟；Ubuntu/AppArmor宿主不满足 userns条件时容器仍 healthy，到首任务才失败：`ci.yml:161-163`、`infra/docker/README.md:95-108`。 |
| P1-R8-QUALITY-01 | 官方 `scripts/check_complexity.py` 当前失败，报告 11 NEW、7 WORSE；unit集合还因 dev Compose密码合同误判 `$${REDIS_PASSWORD}` 固定红 1 条。当前 CI门禁不是全绿。 |

## 7. P2 合同、审计与运维问题

| ID | 问题与证据 |
|---|---|
| P2-R8-01 | Gateway身份回退、监听面和失败审计仍需收紧；异常认证路径与正常拒绝未形成统一可查询审计。 |
| P2-R8-02 | redispatch坏 payload可使整批合法任务反复失败，缺逐条隔离。 |
| P2-R8-03 | 登录、refresh、改密、重置、批量用户与高风险管理操作的审计链不完整。 |
| P2-R8-04 | 登录 User-Agent未按数据库长度合同限制，合法 schema输入可在持久化时500。 |
| P2-R8-05 | email不验格式且只做应用层先查后写，没有数据库唯一约束。 |
| P2-R8-06 | 弱密码与不存在用户的 reset被 broad catch错误映射为500。 |
| P2-R8-07 | 用户身份在缓存/数据库双权威间可静默权限降级。 |
| P2-R8-08 | 认证基础设施异常被伪装为密码错误并参与账户锁定。 |
| P2-R8-09 | 用户不存在与密码错误在响应时间/错误路径上仍可枚举。 |
| P2-R8-10 | 批量用户操作可报告虚假成功或保留旧缓存。 |
| P2-R8-11 | 前后端用户更新字段、状态码与错误合同不一致。 |
| P2-R8-12 | redispatch重试次数存在 off-by-one语义。 |
| P2-R8-13 | Repository与Rule表单仍会覆盖用户输入或限制合法选择。 |
| P2-R8-14 | Worker `print-config`、安装命令和进程参数仍可能暴露连接凭据。 |
| P2-R8-15 | 审计 keyset缺同时间戳 ID条件；HTTP cursor不可达；在线用户统计不等于有效会话。 |
| P2-R8-16 | 非法日志筛选参数被 broad catch改写为500。 |
| P2-R8-17 | Heartbeat的 HMAC-only说明与允许 API Key的请求 schema冲突。 |
| P2-R8-18 | 安装 Key迁移长期保留 used/expired明文凭据。 |
| P2-R8-19 | Project名称数据库全局唯一，与按用户隔离的公开合同冲突。 |
| P2-R8-20 | `max_instances > 1` 时非最新 run在计数更新前返回，成功/失败次数漏记：`execution_status_service.py:358-392`。 |
| P2-R8-21 | 本地备份直接写最终文件、无校验/原子完成/retention，可留下伪完整 dump并耗尽磁盘。 |
| P2-R8-22 | arm64只构建/扫描未运行功能测试；Docker基础镜像未纳入自动安全更新闭环。 |
| P2-R8-23 | Gateway未只读根；Compose不强制 image digest；README声明的 secret边界与实际注入不符。 |
| P2-R8-24 | 滚动升级中存量低 sequence Lease在当前 TTL窗口内仍可能被历史 epoch-ms `lease_gen` 拒绝。 |
| P2-R8-25 | Direct允许 `sequence=0`和 10,001 items且缺 item/batch字节上限，Gateway要求正序列和严格限额，两种 transport行为分叉。 |
| P2-R8-26 | 自动 retry把累计次数写到 source与新 run后再求和，一次重试统计成两次；手动 retry无 linkage反而不可见。 |
| P2-R8-27 | 取消待 retry会用“重试已取消”覆盖原失败原因，却保留矛盾的 `result_data.retry_intent`。 |
| P2-R8-28 | 下调/禁用 retry配置不约束存量 intent；暂停任务的 intent还会每30秒无限重排。 |
| P2-R8-29 | `execution_strategy=specified` 不要求 `specified_worker_id`，可创建每次调度都失败的任务。 |
| P2-R8-30 | heartbeat的 `os_version/python_version/machine_arch` schema无长度上限，与数据库 100/20/20不一致，边界输入可500。 |
| P2-R8-31 | 监控 Stream三表顺序提交且无 message ID唯一约束，部分失败重放会复制已提交数据。 |
| P2-R8-32 | ProjectResponse把内部 `bound_worker_id` 主键当公开 ID返回，泄露实现标识并破坏 API ID合同。 |

## 8. 验证结果

所有后端 pytest均使用仓库 `.venv` 与 60 秒硬超时。

| 检查 | 当前结果 | 未覆盖/判定 |
|---|---|---|
| ACL/策略/live入口 | 33 passed, 1 skipped | live Redis因缺外部 URL跳过；旧 `%RW` 断言已修正 |
| Direct/ownership/factory | 62 passed | 未覆盖真实 Redis + HTTP控制面完整闭环 |
| SSE/日志链 | 56 passed | 字节账本定向用例通过；未覆盖跨 takeover旧 backlog |
| Result/Lease定向 | 25 passed | 未构造 L1校验后、L2改绑、L1提交的交错 |
| SpiderData/Direct PEL | 80 passed | 未构造 Lease在 Lua写入中途切代和无界队列压力 |
| Gateway transport | 48 passed | 未注入时钟偏移与单路永久订阅故障 |
| 注册/retry | 40 passed | 未覆盖无 install key仅凭 intent恢复、手动/自动竞争 |
| 发布与依赖供应链 | 16 passed；Compose合同 29 passed, 1 failed | 唯一稳定失败是测试把安全的 shell运行时 `$${REDIS_PASSWORD}` 误判为 Compose裸默认值 |
| 前端 | 25 files / 145 tests passed；type-check、lint通过 | 未覆盖浏览器真机的跨标签 refresh/登录/登出竞态 |
| Ruff / `complexity_analysis.py` | 通过 | 不能替代基线门禁 |
| 官方 `check_complexity.py` | failed：11 NEW、7 WORSE | 含生产文件增大、魔法数、构造参数增加和测试文件超限 |
| 全量 unit | 60秒到72%被硬超时终止，已出现3个失败、6个skip | 不存在最终全量绿灯证据；`-x`确认首个稳定失败为上述 Compose合同 |

本轮未执行测试机清库部署、fresh Docker build、真实 TLS/mTLS、备份恢复、浏览器真机、多 Master/多 Web Worker故障注入和压力测试。P0未关闭且质量门禁为红，这些验证当前不能提供发布批准。

## 9. 关闭标准与最终判定

1. 以强制 L1/L2切代测试证明：旧代际已确认日志、新代际首日志和终态结果全部只落一次，旧结果不能覆盖新 Lease，合法 backlog不进 DLQ。
2. standby不得完成 leader-only副作用；PEL在持续流量下有公平预算；恢复扫描必须稳定游标遍历全量。
3. 只有确定性坏消息可终止到 DLQ；基础设施故障恢复后必须继续处理同一权威结果。
4. SpiderData写入、Lease、tombstone和 ownership形成同一原子 fence；reclaim队列服从 Engine背压。
5. 认证、配置、Task/Worker更新使用事务/CAS和字段级写入；多进程共享持久版本，不以陈旧 ORM全行覆盖。
6. 迁移采用唯一 runner、`ON_ERROR_STOP`、ledger/checksum与完整 schema post-check；热表在线建索引。
7. 非 K8s生产验收使用 TLS bootstrap、真实 Redis ACL、同一发布 digest、只读容器和可验证的异机备份恢复。
8. 官方复杂度、type-check、unit、供应链门禁全部恢复为零失败；不得扩大 baseline或删除断言掩盖回退。
9. 仅在干净提交和 fresh环境执行最终验收；round7与本报告全部 P0/P1关闭前，不接受压测结果作为发布证据。

最终判定：**拒绝生产发布，拒绝压测**。K8s完全排除，不参与任何问题、测试或关闭条件。
