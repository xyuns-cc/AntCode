# AntCode 第九轮全面修复与最终复核记录（非 K8s，2026-07-27）

> 对应报告：`docs/code-review-2026-07-27-round9-review.md`
> 代码基线：已净化父提交 `d6ef1e771e5173dea699d36b108d9a726ab53f69` 加本次暂存候选集
> 范围：后端、Worker、Gateway、Web API、Master、前端、Redis/PostgreSQL 合同、Docker Compose、CI、迁移、测试和文档
> 明确排除：Kubernetes、Kustomize、Helm、NetworkPolicy 及所有 K8s 发布路径

## 1. 最终结论

Round 9 报告中的 2 个 P0、14 个 P1、7 个 P2 均已找到对应实现并完成代码级修复；本次继续复核时又发现并修复了以下遗漏：

- Direct transport 合同 fixture 没有可信控制面，导致新安全架构无法被合同测试真实覆盖；
- Gateway 在首次 Lease 前启动两条订阅流，必然以空 lease 请求并短暂进入错误状态；
- Gateway server-stream 在空队列时不发送 initial metadata，健康连接永远不能变为 ready；
- Worker integration 仍包含历史公网 Redis 地址、明文凭据和已经删除的 codec API；
- retry 统计仍按累计计数求和，自动重试重复计数且手动重试不可见；
- 取消 retry intent 仍覆盖终态失败原因并保留矛盾 metadata；
- 下调 retry 上限仍不处理已持久化 intent；
- SSE Redis 恢复协议只有 fake/源码字符串测试，没有真实 Redis 自动化门禁。

当前代码质量门禁为绿色：Ruff、Ruff format、mypy、严格复杂度和 `git diff --check` 全部通过。前端 test、type-check、lint、build 全部通过。真实 Redis 下 transport contracts、Worker integration、Gateway integration 和新增 SSE ledger 测试均通过。

但当前仍不能给出“已证明可生产、稳定且无错误”的结论，原因不是已知代码 P0/P1 尚未修复，而是以下生产验收证据仍缺失：

1. 没有真实 PostgreSQL 测试库，本轮未执行 PostgreSQL migration/integration、TLS 握手和唯一一个 Master 结果去重 integration；
2. 没有真实 Redis Sentinel、Redis Cluster 和 TLS/mTLS 环境，配置与命令合同已验证，但未做握手、故障转移和 Cluster 迁移演练；
3. `npm audit` 需要向外部 registry 发送依赖图，本轮没有获得该外部发送授权，因此依赖公告状态未重新验证；
4. SSE live Redis 已覆盖原子性、账本恢复和并发，但没有执行 AOF/RDB 重启耐久演练；
5. 本次 871 文件候选集已经暂存并通过凭据和空白门禁，但 fresh checkout、最终镜像及远端 CI 仍需由提交后的发布流水线证明；
6. 本地与普通远端分支/标签已经重写；GitHub 只读隐藏引用 `refs/pull/2/head` 至 `refs/pull/12/head` 仍引用旧对象，只有 GitHub Support 或重建仓库才能完成服务端解引用和 GC；
7. 已暴露凭据是否已在外部系统完成轮换无法由代码库证明，轮换完成前旧值必须视为失效要求未闭环。

因此最终判定为：**代码修复、本地可达历史和候选提交的凭据门禁已闭环；生产验收、压测和发布批准仍被 GitHub 隐藏 PR 引用、外部凭据轮换、真实中间件、依赖审计及远端发布流水线阻断。**

### 1.1 Git 凭据净化复核

| 检查对象 | 结果 | 结论 |
|---|---|---|
| 当前 tracked + 非忽略 untracked 候选集 | Gitleaks 8.24.3，`0` findings | 通过 |
| 实际 staged 候选集 | 871 files，Gitleaks `0` findings | 通过 |
| 本地全部可达提交 | 144 commits，Gitleaks `0` findings | 通过 |
| 本地对象库 | `refs/original=0`，unreachable objects `0` | 通过 |
| 普通远端分支与标签 | `main`、`v1.0.0` 和 11 个 Dependabot 分支均已重写并强制更新 | 通过 |
| GitHub 隐藏 PR refs | `refs/pull/2/head` 至 `refs/pull/12/head` 仍保留旧对象 | **未关闭，平台侧阻断** |
| 外部凭据轮换 | 代码库无法验证 | **未关闭** |

安全门禁已加入全历史 Gitleaks CI、通用密钥/凭据忽略规则和候选提交合同测试。CI 的测试认证改为运行时随机值或无密码隔离数据库，不再把固定认证字面量提交到 Git。隐藏 PR refs 不可由普通 `git push --force` 删除，不能用本地扫描为零替代 GitHub 服务端清理。

## 2. Round 9 问题关闭矩阵

| 编号 | 代码状态 | 自动化证据 | 生产验收状态 |
|---|---|---|---|
| P0-R9-01 旧代际已确认日志在 rebind 后进 DLQ | 已修复：每次 Lease generation 保存 ingest cutoff，旧代际 cutoff 前 backlog 可合法落库 | generation database、log integrity、合同测试通过 | 真实 PostgreSQL + Redis L1/L2 强制交错未执行 |
| P0-R9-02 结果校验与数据库更新间可切代 | 已修复：结果来源、generation CAS、状态和 metadata 在权威事务内收敛 | task-run lease fencing、结果 loop 测试通过 | 真实 PostgreSQL 并发交错未执行 |
| P1-R9-01 瞬态基础设施错误被永久 DLQ | 已修复：仅确定性坏帧进入 DLQ；基础设施和发布错误保留 PEL | result loop modernization 通过 | Redis/PostgreSQL 故障恢复全栈未执行 |
| P1-R9-02 Direct SpiderData 非原子 Lease fence | 已修复：Lease、ownership、tombstone、数据和索引同槽 Lua 原子处理 | Spider 专项 205 unit + 13 live Redis | 代码关闭 |
| P1-R9-03 Gateway 用本机时间否定服务端 Lease | 已修复：认证后的 Lease 结果以服务端 Redis TIME 为权威 | Gateway reconnect clock-skew 单测通过 | 代码关闭 |
| P1-R9-04 Gateway 订阅永久失败仍 ready | 已修复：双订阅健康参与 `is_connected` 和状态机 | Gateway 单测 256 passed；contracts 130 passed | 代码关闭 |
| P1-R9-05 Direct reclaim 无界内存队列 | 已修复：有界 queue、按剩余容量 claim，消息不足容量时留在 Redis | pending recovery 与 Worker integration 通过 | 代码关闭 |
| P1-R9-06 注册恢复再次依赖一次性安装 Key | 已修复：先恢复持久化 intent，仅创建新 intent 时要求 install key | registration crash recovery 通过 | 代码关闭 |
| P1-R9-07 dev Direct 缺控制面注册凭据 | 已修复：Compose 明确要求 `ANTCODE_WORKER_KEY` 和公开 API 地址 | Compose 合同通过 | fresh Compose 全栈未执行 |
| P1-R9-08 手动重试不消费自动 intent | 已修复：行锁、intent CAS、新 run 与 outbox 同事务 | manual retry database/intent/race 测试通过 | 代码关闭 |
| P1-R9-09 陈旧 ORM 快照覆盖 Worker result_data | 已修复：按 run 行锁读取并合并 scheduler 字段，不再保存陈旧对象 | result metadata 与调度定向测试通过 | 真实 PostgreSQL 快速回报交错未执行 |
| P1-R9-10 retry 配置整行保存覆盖运行状态 | 已修复：字段更新；本轮进一步与超限 intent 清理合并为单事务 | retry config atomic + database 测试通过 | 代码关闭 |
| P1-R9-11 告警配置只刷新 Web API | 已修复：发送前读取权威配置，`auto_alert_levels` 参与真实发送决策 | alert config refresh 测试通过 | 多 Master 实例传播演练未执行 |
| P1-R9-12 Worker 可跨 run 写 SpiderData | 已修复：底层 ACL 撤权，写操作走 HMAC 控制面和原子 fence | 真实 Redis ACL 集成通过 | 代码关闭 |
| P1-R9-13 Worker 可跨组操作 global control | 已修复：Worker ACL 删除 global stream group/read/ack 权限 | 真实 Redis ACL 集成通过 | 代码关闭 |
| P1-R9-14 CI 质量与依赖门禁红灯 | Ruff/mypy/format/complexity 已修复 | 1073 mypy files；complexity 870 baseline | npm audit 和提交后远端 CI 尚未完成，部分关闭 |
| P2-R9-01 Direct/Gateway Spider 输入合同分叉 | 已修复：共享字段、JSON、尺寸、sequence 和 canonical item_id 校验 | Spider 专项测试通过 | 代码关闭 |
| P2-R9-02 本地备份无 retention/原子文件 | 已修复：`.partial`、`pg_restore --list`、SHA-256、原子改名、retention | Compose/backup 合同通过 | 尚未执行真实恢复演练 |
| P2-R9-03 Gateway 根文件系统可写 | 已修复：生产 Compose `read_only`，仅必要 tmpfs/卷可写 | Compose 合同和实际 render 通过 | 代码关闭 |
| P2-R9-04 retry 统计重复计数 | 本轮补修：按不可变 `retry_source_run_id` 关系统计 | 新增 4 个统计测试通过 | 代码关闭 |
| P2-R9-05 取消 retry 覆盖原诊断 | 本轮补修：事务内清 intent、保留终态 error、写独立取消审计 | 新增 2 个 SQLite DB 测试通过 | 代码关闭 |
| P2-R9-06 下调配置不约束存量 intent | 本轮补修：Task 配置与超限 durable intents 同事务更新 | 新增 2 个 SQLite DB 测试通过 | 代码关闭 |
| P2-R9-07 SSE 恢复缺真实 Redis 门禁 | 本轮补修：新增 live Redis 并发、裁剪、账本损坏恢复测试 | 4 passed | AOF/RDB 重启耐久仍未演练 |

## 3. Lease generation、日志和结果一致性

### 3.1 日志 generation 历史

单个 `TaskRun.lease_id` 无法描述接管时已经确认但尚未消费的旧日志。修复后的协议为每次 generation transition 保存 Redis ingest stream cutoff，并在 PostgreSQL 中保留 generation 历史：

- L1 在 cutoff 之前已进入 ingest stream 的消息，即使 L2 已 rebind，仍可按历史 generation 合法消费；
- L1 在 cutoff 之后产生的新消息被视为 stale generation 并拒绝；
- 多次 L1 -> L2 -> L3 切代分别保存 cutoff，不能只保留最后一个边界；
- cutoff 使用完整 Redis Stream ID 宽度，迁移列不会截断；
- 日志 bad frame 和确定性身份错误仍进入可追踪 DLQ，基础设施错误不被误判为永久坏消息。

### 3.2 结果 generation CAS

结果提交不再采用“事务外 Redis 检查，然后无条件改绑 PostgreSQL”的 check-then-act。当前实现要求：

- Worker ID、Lease ID 和单调 generation 与数据库当前绑定一致；
- rebind 后的旧代际不能把 `lease_id` 写回旧值；
- 状态、终态字段和 result metadata 与 generation 判定进入同一权威事务；
- 迟到结果只得到明确 stale-generation 结果，不能覆盖新代际。

### 3.3 结果与调度 metadata

Scheduler 在分发完成后不再保存派发前读取的整个 `result_data`。`merge_dispatch_result_data()` 在事务中：

1. 按 `run_id` 获取行锁；
2. 读取 Worker 可能已经提交的最新 `result_data`；
3. 只补充当前不存在的 scheduler 字段；
4. 使用受总字节上限约束的合并函数；
5. 按主键更新并校验受影响行数。

该协议避免快速 Worker 在 `XADD` 后立即完成并回报时，被 Scheduler 的陈旧 ORM 快照覆盖 artifacts、stdout、checkpoint 或终态 metadata。

## 4. Direct 控制面和 transport contracts

Direct Worker 的 Lease、ownership、日志和 SpiderData 权威写入已经移出 Worker Redis ACL。Worker 通过带 API Key、HMAC、nonce 和 path identity 绑定的 HTTP 控制面执行这些操作。

原 transport contracts 仍直接构造 `RedisTransport`，没有注入 Direct control client，导致 32 个 setup error。修复后的 in-process control adapter 不是 mock success，而是调用真实生产原语：

- `LeaseStore.grant/revoke`；
- `claim/renew/release_run_ownership` Lua fence；
- `validate_log_batch`；
- `append_fenced_log_batch`；
- 独立 Redis client 生命周期清理。

日志合同现在显式 claim run ownership 后才发送日志，与生产引擎顺序一致。Direct 和 Gateway 共用同一组合同，完整结果为 `130 passed`。

## 5. Gateway 首次 Lease 与订阅健康

### 5.1 首次启动竞态

旧顺序为：连接 channel -> 设置 `_running` -> 立即启动 StreamTasks/WatchControl -> lifecycle 再获取首次 Lease。订阅请求依赖 `_lease_id`，因此首次启动必然有空 Lease 窗口。

当前顺序为：

1. `start()` 只建立认证 channel、stubs 和本地队列；
2. 首次 `lease_renew()` 成功保存 Lease；
3. 幂等启动两条订阅；
4. 两条订阅分别报告 health；
5. channel 存活且两个 subscription 都健康时才是 `ONLINE/is_connected`。

显式 stop/start 且本地仍持有同代 Lease 时会恢复订阅，不会破坏未 ACK 消息重投合同。

### 5.2 空闲 server-stream 永远不 ready

Worker 使用 `wait_for_connection()` 确认 gRPC server-stream 真正建立，而 Gateway 在空任务/空控制队列时一直没有 yield，也没有 initial metadata。结果是健康空闲连接永远不能 ready。

修复后：

- `StreamTasks` 在认证和首次 Lease 校验通过后发送 initial metadata；
- `WatchControl` 在认证、Lease 校验、Redis 可用和 consumer group 初始化后发送 initial metadata；
- fake Gateway 遵循相同握手合同；
- 调用对象创建本身不被当成健康，立即失败的 RPC 仍会进入 `RECONNECTING`。

Gateway/Worker 定向单测为 `256 passed`，完整 transport contracts 为 `130 passed`。

## 6. Retry 最终闭环

### 6.1 手动与自动 intent 竞争

手动 retry、自动 retry 和 outbox 现在共享同一数据库事务与行锁。手动路径消费已存在自动 intent；自动路径发现 intent 已被手动消费时 ACK 自己的 Redis claim，不创建第二个 run。outbox 入队失败会回滚 intent 消费。

### 6.2 正确统计

旧统计对所有 `TaskRun.retry_count` 求和。自动重试会同时增加 source counter，并把累计 counter 写到 child，因此一次 retry 可被算两次，多代误差继续放大；手动 child 的 counter 为 0 又完全不可见。

新统计只把带 `result_data.retry_source_run_id` 的不可变 child run 视为一次 retry attempt，并沿 source 关系解析根链：

- source 上的累计 counter 不再重复计数；
- 手动 retry 即使 counter 为 0 也可见；
- 多代链按真实 child 数统计；
- 成功率以 retry child 为分母；
- 平均值以重试根链为分母；
- 损坏的循环关系显式抛错，不静默产生统计。

### 6.3 取消 intent

取消动作在 TaskRun 行锁事务中完成：

- 清 `next_retry_at`；
- 删除 `result_data.retry_intent`；
- 写 `result_data.retry_cancellation`，记录用户和时间；
- FAILED/TIMEOUT 等终态保留原 `error_message`；
- 仅真正的 PENDING 等待记录转为 CANCELLED；
- 更新行数异常显式失败。

### 6.4 配置下调

重试配置更新和超出新上限的 durable intents 在同一 PostgreSQL 事务中处理。数据库提交后逐个清理 Redis pending；Redis 清理异常会向调用方暴露，数据库仍作为 Master 恢复和拒绝陈旧 claim 的权威状态。

## 7. Redis Sentinel/TLS 和 install-key 迁移

### 7.1 Sentinel/TLS

Redis 配置支持并严格校验：

- `rediss+sentinel` 同时加密 Sentinel 控制面和 master 数据面；
- Sentinel/master 独立 ACL username/password；
- CA 验证和 mTLS client certificate；
- IPv4、IPv6、endpoint、port、db、重复参数和未知参数；
- TLS 与非 TLS 参数冲突；
- authority 和 query credential 日志脱敏。

同步和异步 factory 参数传播测试共 `57 passed`。真实 Sentinel failover 和 TLS handshake 尚未执行。

### 7.2 Worker install-key Redis 迁移

迁移脚本删除 Redis Cluster 不安全的 `RENAMENX`，使用单 key `TYPE/DUMP/PTTL/RESTORE/DEL`：

- TTL 扣除实际迁移耗时；
- 永久 key 保持永久；
- 源 key 在搬迁中到期时不复活；
- 目标内容相同允许中断后幂等重跑；
- 目标内容不同显式冲突并保留两端；
- 全部 Redis key 完成后才进入 PostgreSQL transaction；
- 文档要求停掉 AntCode 进程后离线执行。

定向测试 `11 passed`。真实 Redis Cluster 尚未执行。

## 8. PostgreSQL TLS、JWT 文件密钥和旧库升级

PostgreSQL 连接不再丢弃 TLS 查询参数：

- 严格解析 `sslmode` 和 `sslrootcert`；
- `verify-ca/verify-full` 强制配置 CA；
- 无效模式、重复参数、缺失/无效 CA、冲突组合显式失败；
- asyncpg 使用构造后的 `SSLContext`。

JWT 支持 `JWT_SECRET` 与 `JWT_SECRET_FILE` 二选一。文件不存在、不可读、为空或密钥小于 32 字节时初始化失败。生产 Compose 使用 Docker secret 挂载到 migration 和 Web API。

旧库兼容迁移补充 `workers.api_key_previous_expires_at TIMESTAMPTZ NULL`。相关 DB/JWT/Compose 测试合计 `72 passed`，但真实 PostgreSQL TLS 和 migration integration 未执行。

## 9. 非 K8s 生产 Compose、备份和供应链

本项目明确不使用 K8s，本轮仅维护 Docker Compose 发布路径。

- Gateway 使用只读根文件系统和 `/tmp` tmpfs；
- Worker 注册与 ACK 使用 `ANTCODE_PUBLIC_API_BASE_URL`，生产禁止内部明文 HTTP；
- 所有生产镜像拆分 repository 与 digest，Compose 强制 `@sha256:`；
- 本地备份先写 `.partial`，用 `pg_restore --list` 验证，生成 SHA-256 后原子改名；
- retention 删除过期备份；
- release tag 发布前验证目标 commit 是 `origin/main` 祖先；
- Compose 实际 render 及供应链合同 `42 passed`。

这些合同能防止明显配置漂移，但不能替代真实 restore、磁盘耗尽、权限和灾备演练。

## 10. Worker integration 现代化

旧 integration 测试存在三类严重漂移：

1. 两个 tracked 文件默认连接历史公网 Redis，并包含明文凭据；
2. 测试仍调用已删除的 `TaskDecoder.decode_from_dict` 和 `ResultEncoder`；
3. 某测试错误要求敏感环境变量进入用户子进程，与当前安全边界相反。

修复后：

- 只读取 `ANTCODE_INTEGRATION_REDIS_URL`；
- 未配置 Redis 时只 skip 确实依赖 Redis 的 case，纯执行器/codec 测试继续运行；
- codec 使用当前 protobuf API；
- 敏感环境变量测试改为证明 credential 被阻断；
- Direct 测试使用真实 `LeaseStore`、generation fence、ownership 和 fenced log ingest；
- 结果 stream 使用当前 `PROTO_FIELD` 解码 `TaskStatus`；
- Reclaimer NOGROUP、source bundle 字段、namespace 和 cleanup 合同已更新；
- Git 历史中已明确删除、后被错误恢复的 untracked `test_gateway_mode_e2e.py` 已删除。

真实 Redis 下全部 tracked Worker integration：`134 passed, 1 deselected in 23.82s`。唯一 deselected 是需要隔离 PostgreSQL `antcode_e2e_test` 的 Master duplicate-result integration。

## 11. SSE live Redis 门禁

新增 `tests/integration/gateway/test_sse_event_stream_redis.py`，直接调用真实 Redis Lua，不使用 fake success。覆盖：

- 100 个并发 publish 后 Stream、total、order list、sizes hash 四账本一致；
- `MAXLEN` 裁剪同步删除 order 和 size 条目；
- total 负值时先清瞬时流和辅助账本，再追加新消息；
- sizes hash 丢失时原子重建；
- reset 使用 `XTRIM` 而非删除 Stream key，后续 ID 严格单调；
- 每个保留 payload 均可被 `decode_sse_event` 解码。

本机 Redis 结果 `4 passed`；完整 Gateway Redis integration 为 `17 passed`。AOF/RDB 重启和 Redis 进程崩溃点注入仍属于生产验收缺口。

## 12. 完整验证记录

### 12.1 静态门禁

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m ruff check .` | `All checks passed` |
| `.venv/bin/python -m ruff format --check .` | `1060 files already formatted` |
| `.venv/bin/python -m mypy packages services scripts tests` | `Success: no issues found in 1073 source files` |
| `.venv/bin/python -m scripts.check_complexity` | `Complexity gate passed with 870 audited baseline findings` |
| `git diff --check` | 通过 |
| Gitleaks：可达历史、候选集、staged 内容 | 三次均为 `0` findings |

复杂度 baseline 仅在没有 `NEW/WORSE` 时收紧，最终从本轮早期 891 降到 870；没有扩大 baseline 掩盖回退。

### 12.2 后端与合同测试

| 范围 | 结果 |
|---|---|
| Core + 顶层 unit | `847 passed` |
| Web API unit | `424 passed, 1 deprecation warning` |
| Gateway/Master unit | `392 passed` |
| Worker + Scripts unit | `752 passed, 6 skipped` |
| Round 9 generation/retry/alert 定向 | `57 passed` |
| 新增 retry statistics/cancellation/config DB | `8 passed` |
| Gateway/Worker 本轮定向 | `256 passed` |
| Boundary | `15 passed` |
| Transport contracts（Redis + fake Gateway） | `130 passed` |
| Worker integration（真实 Redis） | `134 passed, 1 deselected` |
| Gateway integration（真实 Redis） | `17 passed` |
| SSE live Redis（包含于 Gateway integration） | `4 passed` |
| Load-test self checks | `21 passed, 9 deselected` |
| E2E collection | `12 tests collected` |

### 12.3 前端

| 命令 | 结果 |
|---|---|
| `npm test -- --run` | 27 files / `153 passed` |
| `npm run type-check` | 通过 |
| `npm run lint` | 通过，无 warning |
| `npm run build` | 通过，Vite 7.3.6，3404 modules transformed |
| `npm audit --audit-level=high` | 未执行：外部 registry 依赖图发送未获授权 |

构建观察项：最大未压缩 chunk 为 antd 约 1.22 MB、icons 约 0.75 MB；构建未报 size warning，该项不是当前失败门禁。

## 13. 未验证项与发布边界

以下内容必须保持“未验证”措辞，不能由单测、fake 或静态合同推导为已通过：

1. 真实 PostgreSQL migration、TLS CA/hostname 验证、连接池和并发 generation CAS；
2. 真实 Redis Sentinel TLS/mTLS、Sentinel failover；
3. 真实 Redis Cluster install-key migration、slot/failover 和中断恢复；
4. L1/L2 日志 backlog、结果 CAS、SpiderData 在真实 PostgreSQL + Redis 组合环境中的强制交错；
5. npm registry 当前 advisory 审计；
6. SSE AOF/RDB 重启一致性；
7. Compose 全栈 fresh bootstrap、Docker secret、只读根、备份生成和异机恢复；
8. 本次 871 文件候选集提交后的 fresh checkout、远端 CI 和发布镜像包含性；
9. GitHub Support 对 `refs/pull/2/head` 至 `refs/pull/12/head` 的服务端解引用、GC 和缓存清理；
10. 已暴露凭据是否已在外部系统完成轮换。

在上述生产验收完成前：

- 不批准生产发布；
- 不批准以生产结论为目的的压测；
- 可以继续执行隔离环境的功能、故障注入和容量基线测试，但结果必须标注环境边界；
- K8s 继续完全排除，不得作为替代部署路径或关闭证据。
