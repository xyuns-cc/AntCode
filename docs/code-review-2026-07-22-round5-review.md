# AntCode 第五轮修复后复审报告（2026-07-22）

> 复审对象：`3238d39` 及当前未提交工作树；当前状态为 288 个 tracked 修改、1 个 tracked 删除、484 个 untracked 状态项（展开为 546 个文件）。
>
> 复审方式：主审配合多代理，从提交闭包、Gateway/Lease、Direct Redis、状态机、数据库事务、日志/SSE、前端、安全、Worker 沙箱、Kubernetes、迁移、供应链和复杂度等方向交叉复核。
>
> 本轮性质：只读代码审查与本地自动化验证；未修改业务代码，未连接测试机，未执行真实 PostgreSQL/Redis/Kubernetes 全链路或压力测试。

## 1. 执行结论

当前版本仍然**不能生产发布，也不应开始压测**。“已全部修复”的结论不成立。

本轮确认 migration inventory 和 `pyasn1` 漏洞已关闭，原 `time.time()` 生成 Lease 代际的方向错误、Worker `PATH` 劫持、`StreamStatus` 逐帧 Lease fence、runtime-control marker 原子校验等修复也已落地。但是，当前仍同时存在以下发布阻断：

1. 当前工作树不是确定的可提交源码集合，选择 tracked-only 或 `git add -A` 都不能得到可构建、可测试的版本。
2. CI 后端所有主要分片、Ruff、mypy、前端 lint/type-check/build 均明确失败。
3. K8s production overlay 虽可渲染，但配置、mTLS、NetworkPolicy、身份、目录、探针和镜像合同均无法形成可运行闭环。
4. Worker 任务仍可读取并外带 Worker 身份/中间件凭据，且可通过配置绕过沙箱。
5. Lease 切代、消息结算、取消/重派、跨存储提交、日志容量和前端主流程仍有多项 P1 正确性或安全问题。

定向测试通过只证明现有顺序路径，没有覆盖报告中的真实并发交错、崩溃窗口、容量极值和攻击序列。任何审查和测试也不能保证“零错误”；本报告给出的是当前已证实的发布风险及证据边界。

## 2. 本轮确认已关闭

- `migrations/models/migration_cases.py` 已登记 `20260722_add_task_run_lease_gen.sql`，inventory 定向测试通过。
- `uv.lock` 已升级 `pyasn1==0.6.4`；本轮对 149 个锁定依赖执行 `pip-audit`，结果为零已知漏洞，fail-closed 扫描通过。
- Gateway 不再用本机 `time.time()` 生成 `lease_gen`，改取 Lease `granted_at_ms`；但该值不是严格唯一代际号，见 P1-GW-01。
- Worker 沙箱启动器的 `PATH` 劫持已关闭。
- Gateway `StreamStatus` 已逐帧校验 current Lease；该旧问题不再列入遗留项。
- runtime-control marker 已在 Lua 内校验 Lease；最终 ACK 及逻辑过期窗口仍见 P1-GW-04。
- 项目日志导出已有 8 MiB 日志预算，Gateway TaskStatus 已有 1 MiB 单帧限制；累计对象和 execution 仍无总预算。
- `kubectl kustomize infra/k8s/overlays/production`、三套 Compose 的正确组合、Python compileall 和四服务基础 import 均可通过。这些结果仅证明静态解析/当前脏工作树 import，不证明干净构建或运行闭环。

## 3. P0 生产阻断

### P0-01 当前工作树无法形成可发布提交闭包

- 只提交 tracked 修改时，Web API/Gateway 缺 `antcode_core.common.security.network_source`，Master 缺 `antcode_core.infrastructure.redis.url_security`，Worker 缺 `antcode_worker.executor.artifact_collector`；这些生产依赖仍是 untracked。Web API Dockerfile 还 `COPY` 一个 untracked 的 `scripts/__init__.py`。
- `artifact.proto`、生成合同、Gateway 新服务、Master 修复模块等必需代码也仍未跟踪。因此干净 checkout 无法复现当前本机 import 成功。
- 直接纳入全部 untracked 文件又会带入旧 `gateway.proto`、旧 Gateway 服务、已删除的 project version/storage/websocket 模块、13 个 Aerich/MySQL Python migration、旧测试和运行时凭据。
- `contracts/proto/gateway.proto:78` 与 `data.proto:55,114`、`control.proto:41` 在同一 package 重复定义 `TaskStatus` 等消息；`scripts/generate_proto.py:43` glob 全部 Proto，当前 `make proto` 必然出现 duplicate symbol。
- 删除合同要求 project storage/version 路由不存在，但 untracked 的 `version_service.py`、`project_versions.py`、`project_file_service.py` 和 `infrastructure/storage` 又将旧架构带回，直接触发 Web/Core 合同失败。

### P0-02 仓库发布门禁确定失败

- 后端 CI 同款分片均返回非零：最新完整复跑为 Core 36 failed / 710 passed / 11 errors，Web API 4 failed / 365 passed，Gateway+Master 5 failed / 349 passed，Worker+Scripts 1 failed / 690 passed / 6 skipped，Boundary 2 failed / 22 passed。其他复跑计数有所不同，但所有关键分片均稳定返回非零；此处以最后一次完整输出为准。
- 根因不是单一 flaky test：重复 Proto、旧模块/旧测试回流、删除合同冲突、日志合同 `execution_id`/`run_id` 漂移和旧 MySQL migration 均可稳定触发失败。
- Ruff 有 17 个错误；mypy 在 608 个源文件中有 273 个错误，涉及 48 个文件。`ruff format` 通过，不能抵消 lint/type 失败。
- Bandit 还在 untracked 的旧 storage 实现中报告 2 个 High/High MD5 问题；在确定文件去留前，安全扫描结果同样不能签绿。
- 前端 `lint:ci` 因 `Cookies/index.tsx:148` warning 失败；`type-check` 和 `build` 因 `Logs/index.tsx:48,51,89` 三个不存在的合同成员失败。生产 Dockerfile 强制执行这些门禁，因此前端镜像无法构建。
- `services/worker/runtime_data/secrets/worker_credentials.json` 为 untracked、未命中 `.gitignore`/`.dockerignore`、权限 `0644`，且含非空 Worker 凭据。直接 `git add -A` 会把运行时身份加入提交。

### P0-03 Kubernetes production overlay 不可运行

- `infra/k8s/base/configmap.yaml:12-17` 只有 DB/Redis 拆分字段，应用强制要求完整 `DATABASE_URL`、`REDIS_URL`；文档/overlay 又使用错误的 `JWT_SECRET_KEY`、`WORKER_INSTALL_KEY`，并漏关键配置。四个服务和 migration 在配置校验阶段即可失败。
- Gateway/Worker 只挂载 TLS 文件，没有设置代码读取的证书路径；生成 Secret 还缺客户端 CA。Gateway 默认认证开启而 TLS 未生效会拒绝启动，Ingress TLS 终止也不能向 Gateway 提供客户端证书身份。
- default-deny 后没有 PostgreSQL/Redis ingress，也没有 migration/backup egress，业务和 Job 无法连接中间件。
- Web 探针仍指向不存在的 `/health`，Master 仍用错误的 `9100/healthz`；PostgreSQL exec probe 中 `$(POSTGRES_USER)` 不会由 Kubernetes exec 展开。
- 镜像目录属于 UID/GID 1000，Pod 强制 10001-10004。Worker 卷错挂 `/var/lib/antcode`，实际写 `/app/data/worker`；其余服务只读根文件系统又缺 `/app/data`/`/tmp` 可写卷。
- 5 个 Worker 副本共享一次性安装 Key、同一 mTLS 证书和 `emptyDir` 身份目录；只有一个 Pod 能完成一次性注册，重建后也无法恢复独立身份。
- Worker 还被注入整套 DB/Redis/JWT/Encryption 控制面 Secret，破坏 backendless 隔离目标。
- overlay 使用 `antcode/<service>:latest`，与 CI 的 `ghcr.io/<owner>/antcode-<service>` 不一致；无 frontend Deployment；backup 只写容器 `/tmp` 且不上传。

### P0-04 Worker 任务身份隔离失效

- `services/worker/src/antcode_worker/executor/sandbox.py:256-301` 仍 `--ro-bind / /`，遮蔽集合遗漏 `/etc/antcode/tls`、`worker_config.yaml`、单文件凭据及自定义 Secret mount。
- Direct Redis URL 仍明文写入 Worker 配置。`0600` 不能隔离同 UID 的不可信任务，K8s mTLS 私钥也在任务可见根目录中。
- 任意非 `bwrap` 的绝对 `WORKER_SANDBOX_COMMAND` 会被直接前缀执行；例如 `/usr/bin/env` 可在声称启用 sandbox 时完全绕过文件系统和网络隔离。
- 主执行日志链绕过脱敏器，任务可把读取到的身份通过 stdout、Redis、PostgreSQL 和 SSE 外带。
- Rule 代理是无界线程服务器且无传输预算；CPU/内存/文件限制只作用于单进程或外层 bwrap，不覆盖子孙进程聚合、磁盘与输出字节。Go/Node 依赖安装仍在沙箱外使用 Worker 的网络和资源域。
- 动态探针确认：日志背压可静默丢 stdout；kill 失败后 cancel 仍返回成功且进程继续运行。

## 4. P1 高风险问题

### 4.1 Gateway、Lease 与 Direct Redis

| ID | 问题 | 影响 |
|---|---|---|
| P1-GW-01 | `lease_service.py:114` 将 Redis TIME 取整到毫秒，renew 也重写 `granted_at_ms`；PG CAS 在 `run_ownership_service.py:127` 接受 `stored_gen <= new_gen`。同一毫秒内 L1/L2 可得到相同代际。 | L2 先 bind 后，迟到 L1 仍能覆盖 L2；当前测试只覆盖 100/200，不覆盖相等代际。 |
| P1-GW-02 | ownership claim 不检查 TaskRun 终态；Worker 先报终态再 ACK。终态持久化后 ACK 丢失，L2 仍可 reclaim/claim 并启动进程。 | 已完成 run 可重复执行，外部副作用无法由迟到状态吸收撤销。 |
| P1-GW-03 | Direct L2 claim 只写 Redis ownership，没有接线 PG Lease generation bind。 | L2 已执行但状态/日志因 PG 仍绑定 L1 被拒，形成真实副作用与持久状态分裂。 |
| P1-GW-04 | Gateway/Direct task、control、result 的 Lease check 与 XADD/EVAL/XACK 仍分离；runtime marker 只检查 Hash `PTTL > 0`，逻辑过期后还有 5 秒 retention。 | 切代可插入两步之间，旧 L1 仍可写结果、ACK、requeue、DLQ 或提交 runtime result。 |
| P1-GW-05 | self-fence 在 executor cancel=False 或 kill 异常时仍报告成功；终止异常被吞。 | transport 断开并允许 L2 接管，但旧子进程继续运行，形成双执行。 |
| P1-GW-06 | settlement legacy 默认/非法配置均永久 fail-open；Gateway `include_expired=True` 读取 retention Lease，`context.abort` 还可能被宽泛捕获重映射为 `UNAVAILABLE`。 | 旧 consumer 通道长期越权；调用方收到错误成功/错误码并污染 PG 绑定。 |
| P1-DR-01 | ACL 允许 Worker 改自身 Lease、读任意 Worker Lease、写共享 result/log、裸改任意 run owner/Lease 索引、创建 global consumer group。 | 被攻破 Worker 可伪造他人结果/日志、破坏 ownership，并用孤儿 group 阻止 stream 裁剪。 |
| P1-DR-02 | Redis ACL 轮换是 `SETUSER` 后普通 PG save，无行锁/version CAS；control recovery 首扫后即不再扫描旧 generation；Lease migration 不排除 retention 中逻辑过期 Hash。 | 并发轮换可得到 Redis=B/PG=A；旧控制事件永久遗漏；过期 Lease 被重新激活。 |

删除 Worker 后旧 mTLS 证书仍能重新取得 Lease；`GRPC_HOST` 被读取但监听仍硬编码 `[::]`；`StreamTasks.prefetch` 无上限且 pending/live 单轮可达 `2N`；CancelTask/UpdateConfig 缺 Lease fence且 control stream 无界。这四项均为 P1 控制面问题。

### 4.2 状态机、取消、重试与恢复

- 单 run cancel 已修，但 batch cancel、stop、crawl cancel 仍基于陈旧快照决定不发 control；快照后绑定或新建的 run 可在数据库已取消后继续执行。
- dequeue 后 `remove=False` 和 `CANCELLED -> PREPARING` transition 失败仍被忽略，已取消任务可启动 executor。tombstone 仍是进程内 600 秒、结算前单次消费，重启/超时/ACK 失败后失去 fence。
- Redis MULTI/EXEC 重试可重放 XADD，外部尾扫只查有限窗口；dispatch bind 只改 `worker_id`，没有原子消耗状态。FAILED 重派也不复位 dispatch 终态。
- Lease/Worker 校验仍在状态事务外，事务内语义冲突 `return False` 会提交此前更新。新增交错：L2 已取得 Redis ownership、尚未 PG bind 时，过期 L1 终态仍按旧 PG 绑定被接受，随后 L2 继续执行。
- 新消息优先使 PEL 在持续流量下永久饥饿；所有 Master 无 leader gate 执行恢复，固定 limit 无分页；多类失败源不创建 durable retry intent。

### 4.3 数据事务与生命周期

- standby Master 也消费 `task_trigger`；只放入本机 APScheduler 就标 outbox consumed，TaskRun 创建前崩溃会永久丢触发。
- Artifact cleanup 的 statement snapshot 看不到等待锁期间新提交的 snapshot 引用，仍可删掉已被引用 artifact；任务 artifact 与 source artifact 共表，清理也不识别 `TaskRun.result_data` 引用。
- Task/Project 删除提交后才 purge logs；崩溃或 purge 错误会留下不可重试的永久孤儿。项目删除还漏 `task_id=0` batch run 和多类 Redis 数据，late writer 可重建孤儿。
- HTTP batch logs 按组独立提交、整体失败返回 503，且没有稳定 event ID；重试复制已成功组。直接派发与删除竞态还能创建无 FK TaskRun。
- outbox 第五次失败仍写 `consumed_at`；takeover/ACK 交错可留下已 ACK 但永不 consumed 的事件。
- migration inventory 已修，但 13 个 untracked 旧 MySQL/Aerich migration 污染 SQL-only 真源；新索引仍在事务内普通创建，`init_db` 漏 `api_key_previous_expires_at`，升级文档漏 Worker 凭据迁移。
- Gateway batch 使用 `task_id=0`，SpiderData ownership 强制查询真实 Task，数据上报必然拒绝；空 snapshot subdir 会在重试时漂移。

### 4.4 日志、SSE、容量与安全

- 主 executor stdout/stderr 和完整 argv 绕过 `sanitize_log_message`，可把 token/password 原样写入 Redis、PG、下载和浏览器。
- HTTP 日志一次可物化 10,000 行，每行约 1 MiB；Unicode 和多次 split/join/encode/copy 的极值可达到约 40 GiB 内存量级。配置中的 100 MiB 总限制没有执行点。
- SSE 在预算前物化 25 行；legacy Redis history 可取 200 个 8 MiB 消息。recovery COUNT 使用全余量，broker 连续 drain 1000 条且无字节/时间/权限预算。
- SSE 预算仅计算 content 且先 yield，实际事件帧可超预算；实测 1,048,576 个 NUL 的合法 SSE 帧约 6.29 MiB。ProcessExecutor 默认 StreamReader 约 64 KiB，而日志合同允许 1 MiB 单行，可能触发读取异常并停止 drain。
- 极值 timestamp 不进 DLQ；新消息优先导致 PEL 饥饿；ingest stream 没有高水位。HTTP logs 仍缺 Lease/event_id/封闭 log_type，Master 还存在缺失 `batch_id` 的兼容旁路。
- SSE unsubscribe 先删除本地状态再释放 Redis lease，释放失败后无法重试；replay iterator 又在异常处理边界外，分页漂移或查询异常会裸断流而不是发送 `recovery_unavailable`。
- `result_data` 只有 1 MiB 单帧限制，同一 run 可用不同 key 无界扩大 Redis/JSONB/WAL。Artifact quota 是 4096-run 进程内 LRU，重启、多副本和驱逐可重置；无用户/项目/全局配额。
- 项目导出的 8 MiB 预算只算日志，未覆盖最多 200 条 execution 的完整 `result_data/error/stdout/stderr`。
- 前端 committed + pending 最多 10,000 行，无字节预算；history cursor 后、end 前断线会永久 loading，长离线后不自动恢复，头部 splice/shift 在持续突发流中是 O(N × window)。
- Git repository URL 允许内嵌用户名/密码并原样保存、回显及进入扫描异常；应在输入边界拒绝此类凭据 URL。

### 4.5 前端功能与合同

- 普通用户默认 Dashboard 同时请求 admin-only metrics/stats；一个 403 使整个 `Promise.all` 不提交任何状态，并每 30 秒重复失败。活跃 SpiderStatsTab 用固定倍率和硬编码值伪造速率、健康、趋势、P99、retry、流量等运维遥测。
- 多标签页共享 refresh cookie，rotation 会撤销旧 JTI；旧标签随后 401 登出。SSE 将正常 rotation 的 session revoke 当永久 closed。JWT payload 仍用普通 `atob`，Unicode/base64url 解码失败会形成 refresh 风暴。
- 修改密码后后端撤销全部会话，前端仍保留假登录；restore 在 token 已刷新但 permissions 失败时形成有 token、无 user 的半认证状态。
- ExecutionLogs 不校验 task/run 归属，吞掉加载失败却提示刷新成功，Artifact/SpiderItems 失败被显示为空，SpiderItems 固定只取 200 条。
- Monitor 普通接口与 admin-only 接口耦合；每 30 秒加载 720 小时原始心跳且后端 `.all()` 无界；Worker 日志是合成文本。请求均无 Abort/generation，迟到响应可覆盖新选择。
- 仓库导入请求错误 URL，必然 404；项目上传导入调用已退役接口，固定 410。Agent 项目只是无后端类型/无下一步的假入口；Rule region 收集后没有写入 FormData。
- UserManagement 只搜索当前页，存在迟到响应、跨页选择、双请求/双排序、profile+role 非原子、末页不回退和 modal 重复提交等问题。
- 生产 CSP 固定 `connect-src/img-src 'self'`，与外置 `VITE_API_BASE_URL` 和品牌 logo URL 能力冲突；版本 router 未注册，相关 API 全部 404。

## 5. P2 可维护性、供应链与测试缺口

- `.venv/bin/python -m scripts.check_complexity` 返回通过，但输出是 `1095 audited baseline findings`。commit `b9e0946` 将 baseline 从 396 项扩到 1095 项，只冻结了当前违规，不代表满足 AGENTS.md 硬限制。
- 当前 baseline 包含 54 个 C901（最大 30）、205 个超 300 行文件（最大 `workers.py` 2293 行）、338 个位置参数超限（最大 13）、425 个 magic-number 项；脚本以语句数近似函数长度，未真正执行 50 物理行规则，前端也没有 complexity/max-depth/max-params/no-magic-numbers 规则。
- 新增约 2046 行日志 spool/WAL/archive 实现未接入生产链；旧 websocket、project version/storage、Gateway 单体服务和 Python migration 又作为 untracked 死代码回流，维护边界不清。
- 镜像正式 tag 早于 Cosign 签名，多服务 matrix 不是原子发布；最终 multi-arch 镜像不是被扫描候选的可证明组合，Trivy `ignore-unfixed:true` 放行无修复高危漏洞，独立 security workflow 不在发布 needs 中。
- fresh Compose 文档直接启动空库，Makefile 没指定仓库实际 compose 文件；backup、迁移、真实 mTLS 和 Kubernetes schema/server validation 都没有形成自动验收。

## 6. 自动化验证结果

| 检查 | 本轮结果 | 判定 |
|---|---|---|
| Core / Web API / Gateway+Master / Worker+Scripts / Boundary | 所有分片均有稳定失败，详见 P0-02 | **失败** |
| Ruff check | 17 errors | **失败** |
| mypy | 273 errors / 48 files / 608 source files | **失败** |
| Ruff format | 1020 files already formatted | 通过 |
| 前端 lint / type-check / build | warning / 3 TS errors / 同错误 | **失败** |
| 前端 Vitest | 19 files / 96 tests passed | 通过，但未覆盖浏览器/合同问题 |
| 严格复杂度 | 通过，1095 条 baseline findings | 增量门禁通过，硬规则不合规 |
| Migration inventory | 1 passed | 通过 |
| pip-audit | 149 dependencies / no known vulnerabilities | 通过 |
| npm audit（official registry） | 0 vulnerabilities | 通过 |
| Loadtest 自检 | 21 passed / 9 deselected | 仅工具自检，不是压测 |
| Integration / E2E collect-only | 224 / 12 collected | 仅收集，不是执行通过 |
| 定向 Lease/状态/数据/日志/沙箱/安全测试 | 均通过；关键交错、攻击和极值未覆盖 | 局部通过 |
| Kustomize / Compose parse | 可渲染/可解析 | 静态通过，运行合同失败 |
| compileall / 四服务 import | 当前脏工作树通过 | 不代表干净 checkout |
| `git diff --check` | 通过 | 通过 |

真实 PostgreSQL migration/concurrency、Redis Cluster、fresh Docker/K8s、Gateway mTLS、Worker 凭据不可见性、浏览器 E2E、多标签/SSE、故障注入和压力测试均未执行。当前已有确定失败，因此不应把测试机压测作为替代验收。

## 7. 验收判定

1. 当前版本：**拒绝生产发布**。
2. 当前版本：**拒绝压测**。提交闭包、CI、K8s 和身份隔离已有确定阻断，压测结果不具备上线判定价值。
3. “已全部修复”应撤销；应以本报告 P0/P1 及其并发、崩溃、容量和攻击回归用例作为下一轮验收基线。
4. 只有可提交源码集合闭包、P0/P1 关闭、仓库 CI 全绿，并在测试机 fresh 环境完成真实中间件、mTLS、故障恢复和浏览器全链路后，才具备重新评估生产发布的前提。
