# AntCode 最终修复验收报告（2026-07-13）

## 1. 最终结论

本报告基于基线 `HEAD 71334b9` 与当前未提交修复集，对 2026-07-13 深度审查报告中的问题逐项复核，并补充了安装链、结果来源、SSRF、凭据文件、复杂度门禁和前端超大模块等二次审查发现。

最终结论：

- 原报告中的 **3 个 P0 已关闭**。
- 原报告中的 **15 个 P1 已关闭**。
- 原报告中的 **14 个 P2 已关闭或转为显式、可观测的受限能力**。
- 正式 Unit、Boundary、Contracts、无外部 Redis 的 Worker Integration、Ruff、Mypy、Bandit、复杂度门禁、前端类型/Lint/Build 均通过。
- 代码级发布阻断项已清零，但生产部署仍必须执行数据库迁移、填写强制配置，并在目标环境完成镜像构建与真实 PostgreSQL/Redis 集成验证。

因此，当前修复集可进入发布候选阶段；不能把本地静态与模拟测试结果表述为“生产环境已经完整验证”。

## 2. 审查和修复范围

覆盖：

- Web API、Master、Gateway、Worker、Core、Scrapy、前端。
- PostgreSQL/Redis 一致性、Consumer Group、PEL/XAUTOCLAIM、Outbox、Lease、Retry、取消协议。
- Worker 安装、凭据持久化、身份归属、私有 runtime、API 权限。
- SSRF、DNS rebinding、受控代理、可信代理链、云元数据地址。
- Docker、Compose、GitHub Actions、依赖锁定和安装脚本供应链。
- Artifact、日志、SpiderData 的完整性、故障语义、保留策略和资源使用。
- Python 复杂度、位置参数、文件规模、Mypy 与前端类型覆盖。

## 3. P0 关闭情况

| ID | 最终状态 | 修复结果 |
| --- | --- | --- |
| P0-01 项目依赖安装绕过 | 已关闭 | 项目创建与 Worker runtime 安装统一调用结构化依赖校验；direct source、本地路径、VCS/URL 等入口被拒绝；项目依赖权限和 Worker 执行入口均有回归测试。 |
| P0-02 Sandbox 取消竞态 | 已关闭 | Process/Sandbox 增加明确取消状态；prepare/start 窗口会再次检查取消；只有确认未启动或进程组终止才返回成功；阻塞 prepare 竞态有专项测试。 |
| P0-03 Outbox 首次失败丢失 | 已关闭 | 去掉业务前进程内 seen 提交；新增 PostgreSQL durable inbox/claim/heartbeat/stale takeover/complete 状态，多 Master 和重启可恢复；业务成功后才完成消费。 |

## 4. P1 关闭情况

| ID | 最终状态 | 修复结果 |
| --- | --- | --- |
| P1-01 Rule 默认主链不可用 | 已关闭 | Rule 子进程使用本地 JSONL spool，Worker 父进程通过现有认证 transport relay；Rule 独享受控联网，其余 sandbox 继续隔离网络；TLS/身份配置不再由子进程拼接。 |
| P1-02 Rule 泄露 Worker 长期凭据 | 已关闭 | 子进程不再获得 Worker API Key、Bearer、Redis URL 或 worker_id；父进程代理上报；runtime env 不能覆盖 spool/控制变量。 |
| P1-03 Outbox 多 Master 去重失效 | 已关闭 | durable consumption claim 支持多 Master 竞争、心跳、过期接管和成功完成；不再依赖单进程 LRU。 |
| P1-04 Lease 代际无效 | 已关闭 | TaskRun 持久化 `lease_id`；Worker RUNNING/终态携带 Lease；Master 同时验证 TaskRun Worker 归属、Redis 当前有效 Lease 和消息 Lease；不同代际结果拒绝，Lease token 不写 result_data/日志。 |
| P1-05 follower 消费失败结果丢 retry | 已关闭 | 任意 ResultLoop 可写 durable retry intent；Leader 只负责消费 intent，不阻止 follower 持久化；写 intent 失败时结果消息保留 PEL。 |
| P1-06 retry replay 重复计数/串线 | 已关闭 | retry intent 使用事务 CAS；retry generation 与 source run 绑定；任务级共享 carry 被移除；重复终态不会重复消耗次数。 |
| P1-07 Crawl 部分派发仍 ACK | 已关闭 | seed 直派失败必须获得 durable redispatch intent；未持久化失败会抛错并保留 PEL；已成功 seed 通过 TaskRun 幂等跳过。 |
| P1-08 Crawl 取消伪成功 | 已关闭 | 先向已分配 Worker 写 control，再提交 CANCELLED；任一投递/状态更新失败会保留事件重试。 |
| P1-09 Crawl 请求超时误作任务总超时 | 已关闭 | `batch.timeout` 仅映射 Scrapy DOWNLOAD_TIMEOUT；Worker 任务总超时使用独立常量，不再默认 30 秒杀死慢爬虫。 |
| P1-10 安装 Key 非事务消费 | 已关闭 | PostgreSQL CAS claim、Worker 创建、Key 回写位于同一事务；明文 Key 不再作为数据库条件；并发消费返回明确 409。 |
| P1-11 DLQ 写失败仍 ACK | 已关闭 | Gateway/日志/结果 DLQ 仅在 DLQ 持久化成功后 ACK；失败消息保留 PEL。 |
| P1-12 Worker/runtime 授权旁路 | 已关闭 | 指定 Worker 必须具备 use 权限；私有 runtime 查看、修改、使用均校验所有者或管理员；Worker 只接受可信 `runtime_env_name` 字段。 |
| P1-13 匿名流量触发全局注册熔断 | 已关闭 | 移除全局熔断；按 install-key 摘要 + 来源、以及来源维度独立限流；可信代理 CIDR 和 XFF 从右剥离，无法确认 socket 来源时拒绝。 |
| P1-14 批量取消伪成功 | 已关闭 | 批量操作逐项持久化真实结果；control 发送失败不会返回成功或提前写终态。 |
| P1-15 ownership contention 快速进入 DLQ | 已关闭 | contention 保留原 PEL，不 ACK、不立即 requeue；Gateway 30 秒 visibility 后 XAUTOCLAIM，Direct reclaim 与 3900 秒 ownership TTL 对齐。 |

## 5. P2 关闭情况

| ID | 最终状态 | 修复结果 |
| --- | --- | --- |
| P2-01 Artifact 全量内存 | 已关闭 | asyncpg server-side cursor 顺序读取 chunk；HTTP 下载先写临时文件并校验 size/hash，再交给 FileResponse，损坏数据不会提前发送。 |
| P2-02 日志故障伪装为正常 stderr | 已关闭 | PostgreSQL/Redis 故障使用明确异常语义；不再合成用户 stderr；上层接口可区分空日志与存储故障。 |
| P2-03 Spider 读取失败仍 HTTP 200 | 已关闭 | SpiderData 存储失败不再只写 note；API/前端显式呈现错误，空结果与后端故障可区分。 |
| P2-04 Crawl 限制参数假生效 | 已关闭 | `max_concurrency` 映射 `CONCURRENT_REQUESTS`，`max_depth` 映射 `DEPTH_LIMIT`；max_pages/timeout/delay/retry 全链贯通；合法 0 值不再被 `or default` 吞掉。 |
| P2-05 Spider 默认截断和过期 | 已关闭 | 默认 `MAXLEN=0`、`TTL=0`，不截断、不自动过期；正数配置才启用保留策略；Direct/Gateway/legacy/reporters 语义统一。 |
| P2-06 optional auth 状态不权威 | 已关闭 | 匿名 optional auth 可正常降级；已登录用户复用服务端 session、active 和当前角色校验。 |
| P2-07 前端本地认证状态为权威 | 已关闭 | access token 改为内存；HttpOnly refresh cookie 单飞恢复；用户/权限不持久化；路由等待恢复完成后判定。 |
| P2-08 token localStorage/CSP | 已关闭 | localStorage/sessionStorage 不再保存 access token；HTML/JS 由 nginx 补齐 CSP、HSTS、frame、nosniff、referrer 和 permissions headers；script-src 不允许 inline。 |
| P2-09 安装 Key 明文 Redis/无长度限制 | 已关闭 | PostgreSQL 和 Redis keyspace 都使用摘要；请求模型限制长度；allowed_source 权威持久化 PostgreSQL，Redis meta 缺失不再 fail-open。 |
| P2-10 DNS rebinding | 已关闭 | URL 解析返回 pinned address；HTTP(S) 连接固定已校验 IP，同时保留原 Host/TLS SNI；Git SSH 固定 Hostname/HostKeyAlias；redirect、userinfo、非 HTTP(S)、私网/metadata 均拒绝。 |
| P2-11 构建/CI 供应链漂移 | 已关闭 | 基础镜像固定 digest；Actions 固定 40 位 commit；uv/Node/mise 固定版本；mise 双架构 SHA256；移除 curl-pipe-shell、临时 pip install 和无校验 probe。 |
| P2-12 Secrets 非原子覆盖 | 已关闭 | 同目录 0600 临时文件、fsync、atomic replace、目录 fsync；凭据存储增加 owner/mode、O_NOFOLLOW、硬链接和 64 KiB 限制。 |
| P2-13 删除 Task 留孤儿 | 已关闭 | task_logs/source snapshots/TaskRun/Task 在同一事务；清理异常回滚；内存 Job 只在数据库提交后删除。 |
| P2-14 终态页面持续轮询 | 已关闭 | success/failed/timeout/cancelled/rejected/skipped 均停止自动轮询。 |

## 6. 二次安全复核补充

本轮在原报告之外补充并关闭：

- Direct Redis 结果伪造：Master 不再只信任 payload，必须同时满足 Worker 归属和当前有效 Lease。
- Lease 信息泄露：Lease token 不落 `result_data`，拒绝日志打印 token。
- 可信代理：支持 IP/CIDR，XFF 从右向左剥离可信代理；客户端伪造最左 XFF 无法覆盖真实来源。
- 受控 HTTP 代理：重写 Host，清除外部 `Proxy-Authorization`，云 metadata 即使“允许私网”也永久拒绝。
- Rule 出网：拒绝 `file:`、`data:`、URL userinfo、非 HTTP(S) scheme。
- Spider spool：`O_NOFOLLOW`、普通文件、owner、0600、单硬链接、布尔类型和记录边界校验。
- Worker 凭据：目录 FD 级操作、禁止 symlink/hardlink、owner/mode 校验、原子替换和大小上限。
- Worker 一键安装：脚本端点返回精确字节、SHA256/ETag；命令下载到临时文件、校验摘要后执行；Git source 强制 HTTPS + 40 位 commit，uv 固定版本。
- Worker 重启恢复：凭据保存到 data root 下的 persistent store；文件优先、环境变量只在文件缺失时读取；保存失败显式终止注册。

## 7. 严格复杂度检查结论

严格复杂度检查的方向合理，但原先把 Ruff `PLR0913` 直接当成“最多 3 个位置参数”不合理：

- `PLR0913` 统计总参数，会把 keyword-only 依赖注入参数一起计算。
- 仓库规则要求的是调用方可传的位置参数，不应惩罚明确的 keyword-only config/dependency。
- C901、return/branch/statements 指标适合作为结构热点信号，但不能替代业务审查。

最终实现：

- Ruff `C901 <= 10`。
- Ruff `PLR0911 <= 6`、`PLR0912 <= 12`、`PLR0915 <= 50`。
- Python AST 精确统计位置参数：`posonlyargs + args + vararg`；方法不计 `self/cls`；keyword-only 和 `**kwargs` 不计；阈值为 3。
- 去掉生产目录级 C901 静默豁免。
- 新增函数级、数值化、可审计 baseline；新增、恶化、已消除后重新出现都会阻断 CI；改善必须立即收紧 baseline。

当前存量 baseline：

| 指标 | 数量 |
| --- | ---: |
| C901 | 50 |
| PLR0911 | 20 |
| PLR0912 | 26 |
| PLR0915 | 21 |
| 位置参数 | 312 |
| 合计 | 429 |

这 429 条是显式技术债务，不是“已经符合硬限制”。当前生产 Python/TypeScript/CSS 文件仍有 145 个超过 300 行、68 个超过 500 行。门禁解决的是继续恶化和改进回退问题，不能替代后续按业务边界拆分。

已完成的代表性拆分：

- `Monitor/index.tsx`：1768 行降为 63 行。
- Monitor 新增 TS/TSX 最大 180 行。
- `monitor.css` 拆为 7 个顺序等价文件，全部不超过 262 行；拼接后与原 CSS 24,270 字节完全一致。
- Monitor 全文件移除 `@ts-nocheck`，正式 TypeScript 门禁通过。

## 8. 前端构建性能

- 登录背景从 6144x4096、约 2.0 MiB，压缩为 2560x1706、约 373 KiB；生产构建产物约 382 KiB。
- `antd` chunk 约 1.22 MiB，gzip 约 357 KiB。
- icons chunk 约 748 KiB，gzip 约 163 KiB。

icons 仍大的直接原因是品牌配置允许后端返回任意 Ant Design icon 名，`DynamicIcon` 因而需要整库动态映射。将其改为白名单会改变现有功能，本轮没有以静默功能降级换取体积数字。该项保留为 P3 性能债务，不阻断本次功能和安全发布。

## 9. 最终验证结果

| 门禁 | 最终结果 |
| --- | --- |
| Unit | 910 passed，1 skipped，21.34 秒；60 秒硬超时 |
| Boundary | 15 passed |
| Contracts | 52 passed，70 skipped |
| Worker Integration | 54 passed，64 skipped |
| 安全定向 | 90 passed |
| Ruff | 通过 |
| Ruff format | 626 files already formatted |
| Mypy | 634 source files，0 errors |
| Complexity | 429 audited baseline findings，门禁通过 |
| Bandit HIGH/HIGH | 0 High |
| uv lock | `uv lock --check` 通过 |
| Compose | 带强制密码的 `docker compose config --quiet` 通过；缺失密码 fail-fast 契约通过 |
| Frontend type-check | 通过 |
| Frontend ESLint | 0 warnings，通过 |
| Frontend build | 3291 modules，生产构建通过 |
| CSS 等价 | Monitor 拆分后与原文件 24,270 bytes 一致 |

跳过项说明：

- Contracts 的 70 项需要显式外部 Redis/PostgreSQL 契约环境。
- Worker Integration 的 64 项需要 `ANTCODE_INTEGRATION_REDIS_URL`。
- 本机 Docker daemon 未运行，因此没有执行完整多架构镜像 build；Dockerfile digest、mise 双架构资产摘要、Compose 和供应链契约已验证。

## 10. 发布前必须执行

数据库迁移：

1. `migrations/models/20260713_add_scheduler_outbox_consumption.sql`
2. `migrations/models/20260713_add_task_run_lease_id.sql`
3. `migrations/models/20260713_add_worker_install_key_allowed_source.sql`
4. 按 `docs/database-setup.md` 执行 `scripts/migrate_worker_install_keys.py`，将旧 pending Key 迁移为摘要并回填 allowed_source；缺失来源元数据时脚本 fail-closed。

强制配置：

- `POSTGRES_PASSWORD`、`REDIS_PASSWORD` 必须非空。
- Worker 一键安装必须配置 HTTPS `WORKER_INSTALL_SOURCE_URL`、40 位 `WORKER_INSTALL_SOURCE_REF`、固定 `WORKER_INSTALL_UV_VERSION`。
- 公网安装必须使用 HTTPS `API_BASE_URL`。
- 使用反向代理时必须准确配置 `ANTCODE_TRUSTED_PROXIES` 的 IP/CIDR。
- 若启用 SpiderData TTL/MAXLEN，必须显式设置正数并接受对应数据保留策略；默认 0 表示无限保留。

## 11. 显式限制与残余风险

- 安全 spool 模式当前明确拒绝 `resume_enabled=true` 和启用的 credentialed `proxy_config`；不会向 Rule 子进程下发 Redis 或代理长期凭据。依赖这些功能的部署不能把它们视为已支持。
- 429 条历史复杂度 baseline、145 个超 300 行文件、68 个超 500 行文件仍是可维护性债务；CI 已阻止新增和恶化。
- `antd` 与动态图标包仍较大，属于前端性能债务。
- 未在本机执行完整 Docker 镜像构建、真实 PostgreSQL/Redis Contracts 和带 Redis 的 Worker Integration。

## 12. 发布判断

原报告中的 P0/P1/P2 代码级阻断项已经关闭，当前修复集通过正式本地门禁，可作为发布候选。

生产发布的成立条件是：完成第 10 节迁移和配置，并在目标部署环境补跑镜像构建、真实数据存储契约与关键端到端任务链。若部署依赖 Rule resume 或 credentialed proxy，则该能力仍未交付，不能按“全部功能可用”发布。
