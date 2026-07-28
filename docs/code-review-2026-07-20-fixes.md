# 修复与复审报告（2026-07-20）

针对 [code-review-2026-07-17-final.md](code-review-2026-07-17-final.md) 的修复记录，以及修复后按新视角进行的第二轮对抗性复审结论。

## 一、修复范围

原报告 2 P0 / 24 P1 / ~30 P2。本轮处理结果：

- **P1：24 项全部修复**（含 SEC-01 导出越权）。
- **P2：可代码修复项全部修复**；纯文档/部署项按性质处理（见第四节）。
- **P0：2 项均未处理** —— k8s 生产 profile 与 Rule 沙箱进程级隔离属于基础设施/部署决策，超出本轮代码修复范围，仍为生产阻断项。

### 关键修复摘要（按子系统）

**Gateway（GW 系列）**
- `run_ownership_fence.py`（新增，Gateway/Direct 共用）：CLAIM/RENEW Lua 内嵌权威 Lease Hash 校验（lease_id 匹配 + PTTL 未入 retention 窗口），堵死 P1-GW-04 的 check-then-act TOCTOU；同 worker 旧代际 token 原子接管；`LEASE_STALE` 显式结果。
- requeue 幂等（P1-GW-01）：`_REQUEUE_SETTLE_LUA` 将 XPENDING 归属校验 + XRANGE + XADD + XACK 收进单个 Lua；ACK 时 `XACK=0` 视为幂等成功，不再重复 requeue。
- 日志幂等（P1-GW-02）：`batch_id` 进入 proto 契约（`LogBatch.batch_id`），Worker 端确定性 content-hash 生成，`event_id = batch_id:index` + PG 部分唯一索引 ON CONFLICT。
- StreamTasks 读取后二次 Lease 校验；AckTask 强制携带 `lease_id`（proto `AckTaskRequest.lease_id`）。
- 逐帧限流（P1-GW-05）：`_metered_requests` 对 stream_unary/stream_stream 每帧计费。
- Worker 身份匹配改为精确匹配（等值 / `worker:{id}` 全串 / URI SAN 末段），消除前缀混淆。
- Artifact 上传改流式 `write_blob_from_file`，不再整块进内存。

**Direct Redis（DR 系列）**
- ownership 走 transport 自己的 client/namespace（P1-DR-01），无全局回退。
- 坏帧不再无限重投：task 坏帧原子进 DLQ（`dead_letter_owned`），control 坏帧隔离 ACK；`ValueError/KeyError/TypeError` 判定永久坏帧。
- 日志大小契约与 Gateway 统一：共享 `transport/log_batches.py`（含 batch_id 占位符参与 ByteSize 预算）。
- Redis Cluster：`co_slot_hash_tag` 帮助函数 + DLQ key 按队列独立同 slot；ownership/lease 同 `{ns}` hash tag。
- ACL：`run:owner:*` 增加 SET/GET/DEL/PEXPIRE 与 EVAL selector（EVAL selector 覆盖 Lua 全部 key）。

**SSE 日志流**
- PG 提交顺序游标：`append_entries` 事务内按 run 排序取 `pg_advisory_xact_lock`，`task_logs.id` 单 run 提交有序，游标不再跳行。
- active gap 补发增加行数（2000/轮）与字节（4 MiB/轮）双预算 + `gap_backlog_pending` 立即重扫；状态变更全量补偿。
- 容量限流 key 补 TTL；broker `shutdown()` 释放全部租约并接入 lifespan。

**取消/重试/调度/Worker 生命周期（FN 系列）**
- 取消重试先落 DB durable intent 清除（CAS：仅 PENDING+有计划 → CANCELLED；否则仅清意图），再尽力清 Redis。
- `manual_retry` 仅接受终态集合，绝不改写旧 run；Master `_validate_retry_source` 拒绝 CANCELLED 来源；PENDING 来源废弃时终结为 FAILED。
- result_loop 无 durable 证据不 ACK 重试意图。
- 调度激活失败显式置 `_activation_failed` + readiness 探针；单任务加载隔离；`max_instances` 尊重任务配置。
- Worker 删除：预检活跃 run + 事务内行锁 + 复检；install key 级联删除保留 expired 绑定以便清扫重试。
- web_api 分发补偿 `_finalize_failed_dispatch_run`；最终分发写入改 CAS（dispatch_status=PENDING 守卫）。

**数据事务（DB 系列）**
- outbox 使用 DB 时钟（`SELECT NOW()`），`consume_attempts` 与发布尝试分离（新增迁移 `20260713`/`20260720` 系列，DO-block 类型前置守卫）。
- artifact 清理收敛为单条 CTE（FOR UPDATE + 级联删除 + 计数）。
- source bundle 分发 snapshot-first + get_or_create 首写胜。
- 项目删除：事务内项目锁 + 任务锁 + 活跃校验 + CrawlBatch 清理。
- Redis client 竞态：连接锁 + 陈旧 client 关闭 + 健康检查任务守护。

## 二、第二轮对抗性复审（新视角）

用户指出此前多轮检查仍有遗漏，本轮改用五个此前未覆盖的破坏性视角，各自独立审查：

1. **响应丢失**（结果/ACK 在途丢失后的行为）
2. **切代竞态**（lease 代际切换窗口内新旧进程并发）
3. **晚提交可见性**（先提交后可见 / 迟到写入）
4. **崩溃恢复**（每个多步流程在任意步骤崩溃后的收敛性）
5. **跨租户/跨 Worker 边界**

五个视角均确认原 24 项 P1 修复成立。同时发现新问题，**本轮已全部修复**：

| 新发现 | 级别 | 修复 |
|---|---|---|
| F3-A：QUEUED + dispatch PENDING 的 run 若 Master 在派发中途崩溃则永久滞留 | P1 | `reconcile_repairs.repair_stuck_queued_runs`（>900s CAS 转 dispatch FAILED），接入 reconcile 循环 |
| D1-idem：engine 结算(report/ack)零重试，网络抖动即丢结果 | P1 | `_settle_with_retry`（5 次，1s→16s 退避） |
| D2-lease：`_require_current_generation` 静默返回导致旧代际继续 poll | P2 | 新增 `GenerationLostError`，poll 循环捕获后 abort + break |
| D4-idem：失败日志批放回队首会与新日志重组，batch_id 变化导致重复入库 | P2 | `BatchSender._retry_batch` 屏障，失败批原样重发 |
| D2-idem：Gateway transport 缓存 False ACK 结果，重试被短路 | P2 | 仅缓存 True 结果 |
| D4-lease：worker 完成后立即关机，lease 自然过期，合法终态被拒收进 DLQ | P2 | `_validate_result_source`：run 已绑定同一 lease 代际则接受迟到结算 |
| F2-A：Task 卡 busy（最新 run 已终态但 Task 状态未收敛） | P2 | `repair_stale_task_status`（>900s 收敛） |
| F4-A：expired install key 被级联删除后 worker 清扫无法重试 | P2 | 级联删除 `.exclude(status="expired")` + 孤儿清扫 |
| F5-B：分发最终写入无 CAS，与取消竞态互相覆盖 | P2 | dispatch_status=PENDING CAS 守卫 |
| F1-B：取消重试把 FAILED 终态 run 改写为 CANCELLED，制造字段矛盾 | P2 | 状态保持式取消（终态只清意图） |
| F7-A：调度器 activation 失败标志不复位 | P2 | `_deactivate_scheduler` 复位 |
| 全局 control stream 无保留策略 | P2 | `global_stream_retention.trim_global_control_stream`（全消费组安全裁剪） |
| project.py YAML 导出仍是弱引号版（tasks.py 已加固而 project.py 漏掉） | P3 | 共享 `utils/yaml_export.py`，两处统一 |
| fence 模块 docstring 谎称"ownership TTL 短，升级无需迁移"（实际 ~65 分钟） | P3 | docstring 更正 + worker-transport.md 增加升级 drain 指引 |

## 三、验证结果（2026-07-20）

| 门禁 | 结果 |
|---|---|
| 后端单测 `tests/unit` | 1932 passed / 6 skipped |
| 边界测试 `tests/boundary` | 15 passed |
| 契约测试 `tests/contracts` | 86 passed；43 个 `[redis]` 变体因本机 Docker daemon 损坏（Redis 16379 不可达）无法运行，属环境限制而非代码失败 |
| ruff check / format | 全部通过 |
| mypy（packages + services，516 文件） | 0 错误 |
| 复杂度门禁 | 通过（基线 901 项重置于修复终态；新增函数无新超标项，`_validate_result_source` 拆分 `_validate_result_lease` 消除唯一 NEW 项） |
| 前端 tsc / eslint / vitest / build | 全部通过（子代理验证） |

## 四、遗留已知问题（记录未修）

- **P0 ×2（未动）**：k8s 生产 profile 缺失；Rule 沙箱与 Worker 同进程无硬隔离。
- **D1-lease（P2，部分处理）**：滚动升级期间旧 ownership key（约 65 分钟 TTL）不可见窗口 —— 已在 fence docstring 与 worker-transport.md 写明 drain 指引，代码层不做迁移。
- **P3 若干（记录在案）**：fake_gateway 测试替身保真度；Direct spider item 无去重；LogEntry encode 时间戳回退值；SSE replay 不重发布状态；DLQ `source_missing` 可能由 MAXLEN 裁剪触发；core retry_service 的休眠 drain 副本；admin 依赖环检测全表扫描；`RecoveryWindowError` 个别调用点未捕获；requeue_consumption_failure 的 claim 守卫；artifact 复用与清理竞态；retry-claim 锁序倒置；mTLS namespace 备注。

## 五、环境限制

- 本机 Docker daemon 损坏：`tests/contracts` 的 `[redis]` 参数化变体与 postgres 集成迁移用例需要 `docker compose` 起 Redis(16379)/PG，本轮无法在本机执行；迁移 SQL 已带 DO-block 前置守卫并纳入 `migration_cases.py` 清单（含 `OUTBOX_CONSUME_ATTEMPTS`），需在测试服务器补跑。
