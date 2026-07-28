# AntCode Round 9 SpiderData / Direct ACL 修复记录（非 K8s）

> 日期：2026-07-27
> 范围：SpiderData Direct/Gateway 写入、Lease/ownership fence、Worker Redis ACL、
> batch project 绑定、输入合同、保留策略、旧直写路径与相关测试。
> 明确排除：Kubernetes，以及 round 9 中日志切代、结果 CAS、retry、备份、镜像、
> 前端依赖等其他独立问题。

## 1. 结论

本专项已关闭 round 9 的以下 Spider/ACL 问题：

- `P1-R9-02`：Direct SpiderData Lease 检查与写入之间存在 TOCTOU；
- `P1-R9-12`：Direct Worker 可跨 run 直接篡改 Spider data/meta/index；
- `P1-R9-13`：Direct Worker 可跨组读取、claim、ACK 全局控制消息；
- `P2-R9-01`：Direct 与 Gateway 的 SpiderData 输入限制不一致。

复核修复实现时又发现并关闭了以下遗漏：

- Redis Lua 在 index/order 类型错误或 `__arrival__` 损坏时可能部分提交；
- Direct HTTP 响应丢失后重放会重复 `XADD`；
- 单批 item 数大于 Stream `MAXLEN` 时，旧 marker 裁剪破坏幂等；
- meta 写会延长 item marker、但不延长 Stream，造成假 duplicate ACK；
- project index 用当前 run TTL 清理全部成员，混合 retention 会互相误删；
- project index score 不刷新，活跃 run 可能被提前移除；
- batch-issued `task_id=0` run 只校验“项目存在”，可写入其他项目；
- PostgreSQL 前置发现 stale Lease 时错误返回 HTTP 403；
- Direct meta 接受 Redis 无法编码的容器/null/bool；
- JSON 校验接受 `NaN/Infinity`，深层 JSON 可触发未处理 `RecursionError`；
- Direct、Worker、Gateway 对带空白 `item_id` 的规范化不一致；
- 停用的 Redis sink/reporter 和 Reader mutator 仍保留不可达旧直写逻辑。

专项代码与真实 Redis 8 测试已通过，但这份结论只表示 SpiderData/Direct ACL
边界在当前测试范围内完成修复，不能覆盖 round 9 报告中的其他发布阻断项，
因此不能单凭本记录批准整个项目上线或压测。

## 2. 最终 Redis key 布局

所有参与同一 Spider mutation 的 key 使用 namespace hash tag。namespace 为
`antcode` 时：

| 用途 | Key | 类型 |
|---|---|---|
| item 数据 | `{antcode}:spider:<run_id>:data` | Stream |
| run meta | `{antcode}:spider:<run_id>:meta` | Hash |
| item digest marker | `{antcode}:spider:<run_id>:item-ids` | Hash |
| 当前 Stream 顺序 marker | `{antcode}:spider:<run_id>:item-order` | ZSet |
| 删除 fence | `{antcode}:spider:<run_id>:tombstone` | String |
| 项目活动索引 | `{antcode}:spider:index:<project_id>` | ZSet |
| 项目过期索引 | `{antcode}:spider:index:expiry:<project_id>` | ZSet |
| Worker Lease | `{antcode}:lease:data:<worker_id>` | Hash |
| Lease 撤销集合 | `{antcode}:lease:revoked:<worker_id>` | Set |
| run ownership | `{antcode}:run:owner:<run_id>` | String |

代表性 key 已使用 `redis.cluster.key_slot()` 验证为同一 slot。Direct 和 Gateway
共用 `antcode_core.spider_item_writer.IdempotentSpiderItemWriter`，不再维护两套
不同的 Lua 写入协议。

旧布局 `antcode:spider:{<run_id>}:...` 不会被新代码自动读取或迁移。升级前必须
明确选择导出旧数据或清空旧数据；新旧版本不能滚动混跑，否则会读写两套 key。

## 3. 原子写协议

item Lua 在任何数据 mutation 前完成以下检查：

1. 使用 Redis `TIME` 取得权威时间；
2. Lease ID 不在 revoked set；
3. Lease Hash 的 `worker_id/lease_id` 与请求一致；
4. Lease `expires_at_ms` 未过期，且 `PTTL` 大于 Lease record retention；
5. run ownership token 等于当前 `worker_id + lease_id` token；
6. 永久 tombstone 不存在；
7. stream、marker、order、activity index、expiry index 类型合法；
8. run 的 project、TTL、MAXLEN 与 marker 中已固定的配置一致；
9. 整批 item_id/digest 不存在冲突；
10. `__arrival__` 通过一次 `HINCRBY` 在首个 `XADD` 前完成整数/溢出校验。

通过全部检查后才执行 `XADD/HSET/ZADD/EXPIRE`。Redis Lua 的运行时错误不会
回滚已执行命令，因此“所有可预见错误必须在首个业务 mutation 前暴露”是本协议
的必要条件，不依赖对 Redis Lua 原子性的错误理解。

meta 写入使用同一 Lease/ownership/tombstone fence，并在写入前检查 meta、marker
和两类项目索引的 Redis 类型。item/meta/index 不再通过先检查、后写入、再检查的
三段式调用伪装原子性。

## 4. 幂等与 Stream retention

每个 item 的 digest 覆盖全部 Redis 字段，并使用长度前缀避免字段拼接歧义。
Lua 对同一批次和历史 marker 同时检查：

- 同 `item_id + digest`：返回 duplicate，不执行 `XADD`；
- 同 `item_id` 但 digest 不同：返回 `SPIDER_ITEM_ID_CONFLICT`；
- 同一批内相同 item_id、不同 digest：整批拒绝，首个 item 也不会写入。

digest marker 不再随 Stream `MAXLEN` 删除。`item-order` 只跟踪当前 Stream
保留窗口，marker Hash 在 run TTL 到期或显式删除前保留全部 item digest。
因此即使：

- 首次 Lua 已提交但 HTTP/gRPC 响应丢失；
- 单批 item 数量大于 Stream `MAXLEN`；
- 提交后又有其他批次触发 Stream 裁剪；

原批次重放仍只返回 duplicate，不会再次 `XADD`。当 TTL 配置为 `0` 时，强幂等
marker 与数据一样永久保留，这是“无限保留”配置的显式存储成本。

## 5. TTL 与项目索引

项目索引拆为两个同槽 ZSet：

- activity index score：最后一次成功 item/meta mutation 的 Redis 时间；
- expiry index score：该有限 TTL run 的绝对过期时间 `now + ttl`。

永久 run 不进入 expiry index。每次成功 mutation 先从 expiry index 读取已到期
成员，再同步从 activity/expiry 两个索引移除，最后更新当前 run 的 activity 和
expiry score。这样同一项目可以同时存在：

- `ttl=0` 的永久 run；
- 使用旧长 TTL 的历史 run；
- 使用新短 TTL 的 run。

短 TTL run 不再提前删除永久或长 TTL run，滚动配置变化也不会用当前 run TTL
推断其他成员的生命周期。两个项目索引 key 本身使用 `PERSIST`，只按成员清理。

meta 写会刷新 meta 和项目索引的生命周期，但不会刷新已经存在的 item marker。
item writer 负责让 Stream、digest marker、item-order 使用同一 TTL。由此避免
Stream 已过期、marker 却因 meta 心跳继续存活，随后重放得到“duplicate 成功但
数据实际不存在”的假确认。

显式删除 run 时先写永久 tombstone，再删除 data/meta/marker/order，最后同时
从 activity 和 expiry 项目索引移除 run。late writer 会被 tombstone fence 拒绝，
不能在删除后复活数据。

## 6. Direct 控制面和 Redis ACL

Direct Worker 不再拥有 Spider data/meta/index/dedup/tombstone 或 run ownership
底层权限。item/meta 上报均通过带 HMAC、API Key、nonce replay protection 和
path worker identity 绑定的 Web API 控制面执行。

Worker Redis ACL 同时撤销了共享 `control:global` 的 XGROUP/XREADGROUP/
XPENDING/XCLAIM/XACK/EVAL/XADD/XDEL/XTRIM 权限，广播控制流只能由可信控制面
创建和消费。真实 Redis ACL 测试已验证底层 Spider 写入、ownership 读写以及
全局控制跨组操作均返回 `NoPermissionError`。

HMAC Worker 级限流在签名验证成功后计数。伪造 `X-Worker-ID` 的无效签名请求
不会再消耗受害 Worker 的认证配额。

## 7. project 与输入边界

普通 TaskRun 通过 `Task.project_id -> Project.public_id` 验证请求 project。
batch-issued `task_id=0` run 通过：

`TaskRun.result_data.crawl_batch_id -> CrawlBatch.project_id -> Project.public_id`

验证真实项目。缺少 crawl_batch_id、批次不存在或 project 不匹配均 fail closed，
不再以“请求 project 存在”作为授权依据。

数据库前置校验发现 TaskRun 已绑定其他 Lease 时抛出专用
`StaleSpiderLeaseError`，Direct API 稳定映射 HTTP 412；Lua 竞态阶段发现 stale
Lease 也返回 412。永久 project/item/retention 冲突返回 409，tombstone 返回 409。

Worker、Direct Web API、Gateway 共用严格 JSON 校验：

- 在解析前检查 UTF-8 字节上限；
- 拒绝无效 UTF-8；
- 拒绝 `NaN/Infinity/-Infinity`；
- 捕获深层 JSON 的 `RecursionError` 并返回明确校验错误；
- Direct meta 只接受 string、integer、有限 float，并统一转为 Redis 字符串；
- `bool/null/list/dict/NaN` meta 返回 HTTP 422；
- 三条路径都保存 `strip()` 后的 canonical item_id。

## 8. 旧写入路径

以下类保留名称仅用于向旧调用者提供明确失败，不再包含可达 Redis 直写代码：

- `antcode_scrapy.sinks.redis_sink.RedisSpiderDataSink`；
- `antcode_worker.plugins.spider.data.redis_reporter.RedisDataReporter`。

历史 `TaskType.SPIDER` 与 Rule 子进程只写本地 `0600` spool，父 Worker 通过可信
transport 转发。子进程不再获得 Redis URL、Gateway bearer/API key 等控制凭据，
runtime env 也不能覆盖 spool 控制变量。

`SpiderDataReader` 已删除 `set_config()` 和 `delete_run()` mutator；读取异常不再
吞掉后伪装为空结果。删除必须走带永久 tombstone 的可信 cleanup service。

## 9. 验证证据

最终在当前工作树实际执行：

| 检查 | 结果 |
|---|---|
| Spider/Direct/ACL/worker-auth/scheduler-event 扩大单测 | `205 passed` |
| 本机 Redis 8.0.1 Lua、Gateway reporter、真实 ACL 集成 | `13 passed` |
| 本轮 8 个生产文件隔离 mypy | `Success: no issues found` |
| 本轮相关 Ruff check | `All checks passed` |
| Ruff format | 通过 |
| `git diff --check` | 通过 |

真实 Redis 集成覆盖：

- 首次提交、响应丢失和相同 payload 重放；
- 同 item_id 不同 payload 冲突；
- 单批大于 Stream MAXLEN 后重放；
- marker-order/activity index/expiry index wrong type 无部分写；
- 损坏 `__arrival__` 无 Stream/marker 部分提交；
- meta activity score 刷新但不延长现有 item marker；
- 同项目永久、长 TTL、短 TTL、已过期成员并存与清理；
- tombstone 拒绝 item/meta 且不重建 run key；
- Worker ACL 密码轮换、跨 Worker key 隔离、Spider 底层写入拒绝；
- `control:global` 跨组读取、claim、ACK、EVAL 和 destructive 操作拒绝。

严格复杂度门禁复跑时，本专项文件已无 `NEW/WORSE`。全仓门禁仍被同时修改的
其他模块阻断，当前快照包含 `reclaim.py` 文件行数以及日志、配置、初始化脚本等
非本专项漂移；没有通过修改 baseline、关闭规则或忽略退出码把全仓结果伪装为
绿色。

## 10. 发布边界

本专项不再保留已知的 SpiderData 原子性、跨 run ACL、幂等、project 绑定或输入
合同问题。但 round 9 报告的其他 P0/P1 仍必须分别按其测试和验收条件关闭，尤其
是日志切代、结果代际 CAS、基础设施异常后的结果耐久重试、注册恢复、retry 竞态、
备份恢复以及全仓 CI 门禁。在这些问题关闭前，整体结论仍是不能批准生产发布。

## 11. 2026-07-27 全局最终复核补充

本文件第 10 节记录的是 Spider 专项完成时的全局状态。后续并行修复已经关闭日志
generation、结果 CAS、结果基础设施重试、注册恢复、retry 竞态、备份原子性、
Gateway readiness、Direct transport contracts 和 CI 静态门禁。全局最终状态与
精确测试数字以 `docs/code-review-2026-07-27-round9-fixes.md` 为准。

Spider 专项的实现与测试结论保持不变；当前不批准生产发布的剩余原因已收敛为真实
PostgreSQL/Sentinel/Cluster 验收、npm audit 外部授权、SSE 重启耐久、Compose
全栈恢复演练以及约 852 项脏工作树的提交/镜像闭包，而不是已修复的代码级
SpiderData 问题或 complexity/mypy/format 红灯。
