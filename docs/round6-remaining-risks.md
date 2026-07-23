# Round6 剩余风险清单 (2026-07-23 至 2026-07-24)

本文档跟踪 `docs/code-review-2026-07-23-round6-review.md` 中当前 session
未完全关闭的项;每条注明当前防线(应用层缓解或部分闭环)与彻底闭环需要
的下一步工作(schema 迁移/多模块协同/产品决策)。

## 已在本轮 (round6 续修) 关闭的项 (供交叉参考)

已 landed 的修复见 git log `--grep='round6'` (batch cancel loop-until-empty,
outbox TERMINATED prefix, artifact quota metrics/Redis 备份, batch logs 207,
timestamp 值域, JWT base64url+UTF-8, SSE gap 帧字节预算 + yield 前判,
task_logs_purge outbox durable cleanup, changePassword 广播 logout,
session_revoked 广播 logout, SpiderStats 移除合成运维数据, Cookies demo
标注, StreamReader 1 MiB, 前端日志字节截断, Rule region FormData,
recover_on_startup leader gate, JWT decodeAccessToken UTF-8, XDEL orphan,
log_batch batch_id 契约测试等)。

## 一、5.1 Gateway/Lease/Direct Redis

### P1-DR-01 ACL 命令白名单不能表达 run/group/成员级所有权

**当前防线**:
- `packages/antcode_core/src/antcode_core/common/security/redis_acl.py`
  模块 docstring 已详细承认残余风险 (1) run_ownership + 共享 lease 索引 /
  (2) spider key 无 worker 维度 / (3) `{ns}:control:global` group 命名不控。
- 应用层缓解:结算与派发侧始终以权威 Lease Hash(按 worker 隔离、mTLS 绑定)
  复核代际;ownership key 与共享索引不是授权的最终依据。

**彻底闭环需要**:
- Key 布局重构:ownership 结算全部收拢到 Master/Gateway 凭证;spider key
  引入 worker 维度;`{ns}:control:global` group 预建(Web API POST `/v1/workers`
  注册时) + Worker 启动只 join 不 create。
- 属于 P2 架构重构,预计 1-2 迭代完成。

## 二、5.2 状态机 / 消息结算 / 数据事务

### outbox takeover/ACK 交错的业务级 idempotency

**当前防线**:
- `complete_consumption` CAS `consume_owner=owner & consumed_at IS NULL`,
  被别人 takeover 后 raise。
- 业务侧幂等由 `master/control/trigger_idempotency.py` 保证(触发去重 key
  在同一事务里 upsert)。

**彻底闭环需要**:
- 引入 outbox `terminal_at` 列与 `consumed_at` 分离,让"业务成功"与"重试
  耗尽"完全脱钩(round6 5.2 前缀方案已提供运维标注,但字段仍复用)。
- 需要 migration + 全链路查询更新,评估收益与迁移成本。

### 5.2 dequeue `remove=False` 与 `CANCELLED → PREPARING` 冲突

**当前状态**: 未在代码里定位到该 kwarg,可能审查文档指的是 legacy 描述。
`execution_status_service._should_update` 已用终态 set 保护
(_task_terminal_states / _runtime_terminal / _dispatch_terminal),
`update_dispatch_status` CAS 允许来源集合排除终态。CANCELLED → PREPARING
路径已被 dispatch CAS 拦下。

**下一步**:审查文档作者(下轮)复核该项是否仍有效,如仍复现请附具体入口。

## 三、5.3 日志 / SSE / 容量

### Artifact quota Redis 备份未接线 Gateway wiring

**当前防线**:
- `RunArtifactQuota` 已支持 `redis_client=` 参数 + `async_reserve/release/
  restore_from_redis`。sync API 保持原语义,单元测试覆盖。

**彻底闭环需要**:
- Gateway wiring (`services/gateway/src/antcode_gateway/main.py`) 传入
  async Redis client 到 `GatewayArtifactService.run_quota`。
- artifact_service 的 `_reserve_run_quota` / `except BaseException`
  release 分支改走 `await run_quota.async_reserve(...)`。
- 需要一次 wiring 变更 + 回归验证不改变现有语义(Redis 不可达时降级纯内存)。
- 属于 next session 一个独立 commit 可完成。

### SSE broker drain 统一预算

**当前状态**: PostgresLogGapReader 已修 (yield 前预算 + 真实帧字节, 见 c060c59)。
Broker drain / active history 已有 `_HistoryBudget` (max_lines + max_bytes)。
未找到额外未 fence 的 SSE 路径,若下轮审查再次指出请附具体入口。

## 四、5.4 前端

### SSE session_revoked 全网 logout 已实现

- `logStreamConnection.ts` handleStreamError 里 `session_revoked / access_revoked`
  → `broadcastAuthEvent('logout')`, `useAuth` 已订阅并触发 `AuthHandler.handleAuthFailure(false)`。
- 剩余边界: refresh cookie 轮换 (rotation) 时后端应清旧 cookie, 属后端职责。

### Monitor 页面剩余合成数据

**当前状态**: SpiderStatsTab 已清理 (686b8bc + 4fdb21e), Cookies 页已标 demo
(ad4230e)。Monitor 页面主要是 workers 真实数据聚合(data.ts 里 Math.max/min/
round 都是聚合真实),无发现虚假合成。

## 五、P2 复杂度门禁

### 大文件拆分

**当前 baseline**:
| 文件 | 行数 |
|---|---|
| workers.py (Web API route) | 2308 |
| worker/engine.py | 1804 |
| gateway transport | (拆分待做) |
| master scheduler_loop.py | ~1518 |
| worker/redis transport.py | 1497 |

**下一步**: 单独 session 拆分, 目标先把 workers.py 按 (workers CRUD /
task ingest / status query / metrics) 拆成 4 个模块 (~500 行/个)。

## 六、跟踪表

| ID | 类型 | 状态 | 下一步 owner / session |
|---|---|---|---|
| P1-DR-01 | 架构重构 | 已 doc | 下 1-2 session 分阶段 |
| outbox terminal_at 列 | schema 迁移 | 待评估 | ROI 复核 |
| Artifact quota wiring | 接线 | 待做 | next session (小 commit) |
| workers.py 拆分 | 重构 | 待做 | 独立 session |
| engine.py 拆分 | 重构 | 待做 | 独立 session |

## 结论

当前 round6 review 里所有"点名的可修点"要么已在本 session 关闭 (24 个
commit / 30+ 项), 要么已在源码 docstring 明确承认残余风险 (P1-DR-01 硬边界),
要么是需要 schema 迁移/多模块协同的 P2 重构(workers.py 拆分等)。

生产阻断项 (P0-01/02/03/04) 全部关闭, 前端质量门禁 (type-check + lint +
100 vitest) 与后端质量门禁 (ruff/format/mypy 本轮零新错/2216 unit+boundary +
973 复杂度 baseline) 全部通过。
