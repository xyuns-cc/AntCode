# AntCode 全仓深度复审报告（2026-07-13）

## 1. 审查结论

本轮在 `HEAD 71334b9` 上重新审查 Web API、Master、Gateway、Worker、Core、Scrapy、前端、Redis/PostgreSQL、一致性协议、Docker/CI 和测试门禁，并由安全、分布式正确性、Worker/前端三个代理并行检查，主审逐项复核关键证据。

结论：**不能认定项目已经全部修复，也不应直接发布到生产环境。**

本轮确认的主要问题包括：

- 2 个 P0 发布阻断项：依赖构建逃逸 Worker 任务沙箱；生产默认 SandboxExecutor 无法按真实 `run_id` 取消进程。
- 15 组 P1 高风险问题：Rule 默认链路不可用、任务错误 ACK、Lease 代际竞态、状态回退、重试失效、批次取消伪成功、SSRF、一次性 Key 并发消费、敏感字段明文等。
- 多项 P2 功能、安全纵深、资源和可观测性问题。
- P3 工程硬指标仍严重超限，正式静态门禁通过主要依赖全目录豁免。

7 月 10 日报告中已经关闭的部分问题仍然成立，例如 Web session/JTI、用户禁用与角色实时校验、WebSocket 一次性票据、Gateway principal 与 payload Worker 身份绑定、Gateway 的 DB run ownership 校验。本报告不重复计入这些已排除项。

## 2. 严重度定义

- **P0**：可导致 Worker 主机接管、任意代码逃逸隔离、关键控制面完全失效或大范围不可逆副作用。
- **P1**：可导致生产主链路不可用、任务永久丢失、错误终态、越权网络访问、敏感信息泄露或分布式一致性破坏。
- **P2**：确定的功能错误、安全纵深缺口、数据完整性、资源或可观测性问题。
- **P3**：可维护性、复杂度、类型覆盖和发布工程残余。

## 3. P0 发布阻断项

### P0-01：普通 Worker 使用权限可通过依赖构建在任务沙箱外执行代码

证据：

- [`runtimes.py`](../services/web_api/src/antcode_web_api/routes/v1/runtimes.py#L215) 允许安装任意 `PackageRequest.packages`。
- [`runtime_access.py`](../services/web_api/src/antcode_web_api/routes/v1/runtime_access.py#L18) 对普通用户只要求 Worker `use` 权限。
- [`engine.py`](../services/worker/src/antcode_worker/engine/engine.py#L480) 把控制指令直接交给 `uv_manager.install_packages()`。
- [`uv_manager.py`](../services/worker/src/antcode_worker/runtime/uv_manager.py#L32) 的正则允许 URL、VCS 和 direct reference；本轮实测 `https://evil/pkg.tar.gz`、`git+https://evil/repo.git`、`pkg@https://evil/x.tar.gz` 均通过。
- [`uv_manager.py`](../services/worker/src/antcode_worker/runtime/uv_manager.py#L417) 调用 `uv pip install`；[`run_command()`](../services/worker/src/antcode_worker/runtime/uv_manager.py#L77) 以 Worker 主进程 UID、主网络和主文件系统视图启动，不经过任务 SandboxExecutor。

触发链：

```text
普通用户取得 Worker use 权限
-> POST /workers/{id}/runtimes/{env}/packages
-> 提交恶意 sdist / PEP 517 backend / VCS dependency
-> Worker 主进程在沙箱外执行 uv pip install
-> 构建后端以 Worker UID 执行任意代码
```

影响：可读取 Worker API key、identity、Redis/Gateway 凭据，篡改共享 runtime、植入持久化代码，并接管后续任务执行。包名 argv 未发生 shell 注入不影响该结论，风险来自 Python 构建协议本身允许执行构建代码。

现有测试只验证 runtime builder 被调用和路径格式，没有证明依赖来源可信、只允许 wheel、构建阶段隔离或普通用户不能执行管理操作。

修复方向：依赖管理必须是管理员或受信发布角色操作；构建在一次性、无 Worker 凭据、无宿主写权限的隔离环境中执行；建立允许的 index/source/wheel/hash 策略，明确拒绝 URL、VCS、sdist 和任意 build backend。

### P0-02：生产默认 SandboxExecutor 无法按真实 run_id 取消进程

证据：

- [`sandbox.py`](../services/worker/src/antcode_worker/executor/sandbox.py#L404) 外层使用 `plugin_name` 而不使用 `exec_plan.run_id`。
- [`sandbox.py`](../services/worker/src/antcode_worker/executor/sandbox.py#L482) 重建 `ExecPlan` 时没有复制 `run_id`。
- [`process.py`](../services/worker/src/antcode_worker/executor/process.py#L247) 因此回退到 `plugin_name` 注册进程；[`process.py`](../services/worker/src/antcode_worker/executor/process.py#L313) 最终以 `code`、`rule` 等共享键保存任务。
- `SandboxExecutor` 自身没有调用 `_register_task()`；[`BaseExecutor.cancel()`](../services/worker/src/antcode_worker/executor/base.py#L190) 先查外层空的 `_running_tasks`，找不到真实 `run_id` 就返回 `False`，不会进入 [`SandboxExecutor._do_cancel()`](../services/worker/src/antcode_worker/executor/sandbox.py#L498)。
- [`Engine.cancel()`](../services/worker/src/antcode_worker/engine/engine.py#L968) 始终用真实 `run_id` 调用 executor。

本轮复现：`cancel(real_run_id) == False`，内层注册键为 `['code']`。

影响：API/DB 可以先显示 `CANCELLED`，但用户进程继续写库、访问外网或产生其他副作用；同一插件并发任务相互覆盖，先结束的任务还会注销另一个仍在运行的进程；重派后可形成旧进程与新进程双跑。

现有取消集成测试只覆盖 `ProcessExecutor`，没有覆盖生产默认 `SandboxExecutor`，且相关集成文件在缺少 Redis 时整体跳过。

修复方向：SandboxExecutor 必须以真实 `run_id` 注册自身任务并原样传递全部 ExecPlan 隔离/资源字段；取消、超时、缩容和 shutdown 统一验证真实进程组已终止；增加同插件双并发取消测试。

## 4. P1 高风险问题

### P1-01：默认 Gateway + Sandbox 的 Rule 项目主链路不可用

当前默认配置同时启用 `sandbox_mode=sandbox`、`sandbox_network_isolated=true` 和 `transport_mode=gateway`：[`config.py`](../services/worker/src/antcode_worker/config.py#L248)。bwrap 在 [`sandbox.py`](../services/worker/src/antcode_worker/executor/sandbox.py#L239) 加入 `--unshare-net`，Rule 因而既不能访问目标站点，也不能访问 Gateway/Redis sink。

即使关闭网络隔离，认证链仍断裂：

- [`rule/plugin.py`](../services/worker/src/antcode_worker/plugins/rule/plugin.py#L128) 只读取未建模、示例配置未声明的 `WORKER_GATEWAY_AUTH_TOKEN`，没有取得正常注册产生的 API key。
- [`rule/plugin.py`](../services/worker/src/antcode_worker/plugins/rule/plugin.py#L145) 写入 `ANTCODE_WORKER_ID`，但 [`sandbox.py`](../services/worker/src/antcode_worker/executor/sandbox.py#L34) 白名单误写成 `ANTCODE_SPIDER_WORKER_ID`；本轮实测过滤后 Worker ID 消失。
- [`gateway_sink.py`](../packages/antcode_scrapy/src/antcode_scrapy/sinks/gateway_sink.py#L37) 回退为 `unknown`，且 [`gateway_sink.py`](../packages/antcode_scrapy/src/antcode_scrapy/sinks/gateway_sink.py#L243) 不发送正常 API key 所需的 `x-api-key`、`x-worker-id`。
- [`data_service.py`](../services/gateway/src/antcode_gateway/services/data_service.py#L291) 要求认证主体、batch Worker ID 和 TaskRun ownership 一致。

结果是 Rule 任务要么无法联网，要么 SpiderData 被 Gateway 拒绝。若运维手工注入 Worker 级 Bearer token，又重新打开旧 P0-05 的全 Worker 凭据暴露问题。

### P1-02：stale run ownership 竞争时错误 ACK 可永久丢任务

[`engine.py`](../services/worker/src/antcode_worker/engine/engine.py#L316) poll 后先写本地状态，再用 Redis `SET NX` claim ownership。若 ownership 已存在，代码在 [`engine.py`](../services/worker/src/antcode_worker/engine/engine.py#L340) 直接以 `accepted=True` ACK 当前 receipt。

时序：W1 claim 后崩溃且未执行/未 ACK；W2 或重启实例 reclaim 同一 PEL；旧 ownership TTL 仍在，claim 返回 False；W2 ACK 原消息；ready stream 中的唯一任务被删除且无人执行。现有测试只覆盖 Redis 不可用 fail-closed 和 namespace，不覆盖 stale owner + reclaimed receipt。

修复方向：ownership contention 不能确认业务消息完成；必须验证持有者 lease/epoch，保留 PEL 或进入显式延迟重试，只有确认另一活实例已经 durable 接管后才允许 ACK。

### P1-03：Lease eviction 回调可误杀同 Worker 新 lease 下的新任务

[`lease_service.py`](../packages/antcode_core/src/antcode_core/application/services/lease_service.py#L551) sweep 只返回 `worker_id`，丢失被删除的 `lease_id`/epoch；[`lease_sweeper_loop.py`](../services/master/src/antcode_master/control/lease_sweeper_loop.py#L117) 随后异步执行回调；[`master/__main__.py`](../services/master/src/antcode_master/__main__.py#L54) 仅按 Worker ID 查询全部 RUNNING TaskRun 并置 FAILED。

旧 lease 被 sweep 后，同一 Worker public_id 可以立即获得新 lease并启动新 run；旧 eviction callback 稍后执行时无法区分代际，会把新 run 误判为失联任务。TaskRun 未持久化 lease/fencing epoch，Gateway 数据面也只校验 Worker ID 和 DB ownership，旧实例凭长期凭据仍可继续写状态/日志/SpiderData。

### P1-04：Task 状态可被同一 run 的陈旧快照从终态回退

[`execution_status_service.py`](../packages/antcode_core/src/antcode_core/application/services/scheduler/execution_status_service.py#L307) 成功 CAS 后在事务外重新读取 TaskRun，再进入 [`_sync_task_status()`](../packages/antcode_core/src/antcode_core/application/services/scheduler/execution_status_service.py#L317)。该函数只锁 Task，并确认 latest run ID 相同，随后直接使用调用方传入的 `execution.status`。

并发时，A 可取得 RUNNING 快照，B 把同一 run 更新为 SUCCESS 并先同步 Task=SUCCESS，A 后获得 Task 锁并用陈旧快照覆盖为 RUNNING。TaskRun 终态与 Task 非终态永久不一致，成功/失败计数也可能漂移。

### P1-05：Worker 上报 FAILED/TIMEOUT 不进入业务 retry

[`result_loop.py`](../services/master/src/antcode_master/ingester/result_loop.py#L238) 对远程终态只调用 `task_run_service.update_result()`。`scheduler_loop._schedule_retry()` 只覆盖派发/本地即时失败路径，远程 Worker 已 ACK 后的 FAILED/TIMEOUT 没有调用 retry 服务。

因此用户设置的 `Task.retry_count` 对主要分布式执行失败无效。现有测试没有“Worker 上报 FAILED -> durable retry intent -> 新 TaskRun”的端到端场景。

### P1-06：已进入 retry 的任务计数在新 TaskRun 中归零，可无限重试

[`scheduler_loop.py`](../services/master/src/antcode_master/control/scheduler_loop.py#L918) 每次 trigger 新建 TaskRun 时固定 `retry_count=0`；旧 run 的计数只被写入 retry 队列；[`retry_loop.py`](../services/master/src/antcode_master/control/retry_loop.py#L510) 到期后只调用 `trigger_task(task_id)`，没有把累计计数传入新 run。

若失败路径恢复接线，每个新 run 又满足 `retry_count < task.retry_count`，最大重试次数可被无限突破。

### P1-07：Crawl batch 取消只改数据库，不停止 Worker 进程

[`batch_service.py`](../packages/antcode_core/src/antcode_core/application/services/crawl/batch_service.py#L673) 把批次改为 CANCELLED 并发出事件；[`batch_dispatcher_service.py`](../packages/antcode_core/src/antcode_core/application/services/crawl/batch_dispatcher_service.py#L121) 只把活跃 TaskRun 标为 CANCELLED，文件在 [`L154`](../packages/antcode_core/src/antcode_core/application/services/crawl/batch_dispatcher_service.py#L154) 明确说明未发送 Worker control。

运行中的爬虫继续访问目标站、写外部副作用和 SpiderData，只是迟到终态被数据库吸收。单 run/task 的取消路径已经会发送 control，batch 路径没有复用。

### P1-08：Crawl 派发失败被吞，事件仍 ACK，seed 不会自动重试

[`batch_dispatcher_service.py`](../packages/antcode_core/src/antcode_core/application/services/crawl/batch_dispatcher_service.py#L99) 汇总 `failed` 后只记录日志；[`_dispatch_single_url()`](../packages/antcode_core/src/antcode_core/application/services/crawl/batch_dispatcher_service.py#L245) 在 dispatch 与 redispatch 都失败时删除占位并返回 False；外层异常也在 [`L261`](../packages/antcode_core/src/antcode_core/application/services/crawl/batch_dispatcher_service.py#L261) 被吞。

调用方 [`scheduler_event_loop.py`](../services/master/src/antcode_master/control/scheduler_event_loop.py#L296) 看到正常返回后 ACK `batch_started` 事件。未派发 seed 没有 durable intent，只能依赖人工 resume，30 分钟后 batch 被状态 loop 标 FAILED。这与文件“异常上抛、PEL 重投”的注释相反。

### P1-09：规则项目测试连接是 live blind SSRF

[`project.py`](../services/web_api/src/antcode_web_api/routes/v1/project.py#L1001) 允许项目 owner 触发连接测试；`target_url` 在 [`project schema`](../packages/antcode_core/src/antcode_core/domain/schemas/project.py#L232) 只有长度限制。Web API 在 [`project.py`](../services/web_api/src/antcode_web_api/routes/v1/project.py#L1021) 直接对该 URL 发 GET/POST，并携带项目自定义 headers/cookies。

普通项目 owner 可请求 localhost、RFC1918、link-local、云 metadata 或内部控制面，执行端是 Web API 所在网络。异常又被转换成 HTTP 200 的 `success=false`，会掩盖基础设施错误。

### P1-10：Worker 一次性安装 Key 并发消费不原子

[`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L1184) 先读取 pending Key；[`_claim_install_key_source_once()`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L262) 只绑定来源和 nonce，同一来源使用不同 nonce 会同时通过；Worker 创建完成后才在 [`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L1248) 调用无条件 `mark_used()`。

两个同源并发请求可都越过 pending 检查，各自创建 Worker 并拿到一套 API key/secret。现有测试只 mock 相同 nonce 被拒，没有真实数据库 CAS 或并发测试。

### P1-11：Git/Webhook SSRF 校验存在解析与连接 TOCTOU

[`git_url_security.py`](../packages/antcode_core/src/antcode_core/application/services/projects/git_url_security.py#L47) 只在调用前解析一次 hostname。Git 随后由 libcurl 重新解析并可能跟随 redirect；告警 webhook 在校验后由 httpx 再次解析目标。攻击者控制 DNS 时可首轮返回公网地址、连接时返回私网地址；Git 还可能 30x 到内网/metadata。

现有测试只覆盖初始 URL 和初次 DNS 结果，没有 redirect、DNS rebinding 或连接目标固定测试。告警 httpx 默认不跟随 redirect，缩小了 redirect 面，但 DNS TOCTOU 仍存在。

### P1-12：Task 执行参数和环境变量仍明文存储

[`task.py`](../packages/antcode_core/src/antcode_core/domain/models/task.py#L51) 的 `execution_params`、`environment_vars` 仍使用普通 JSONField。创建、复制和详情接口会持久化并返回其中的 token、proxy、密码等值；[`encrypt_sensitive_data.py`](../scripts/encrypt_sensitive_data.py#L21) 也没有迁移 scheduled tasks。

因此 7 月 10 日“runtime/env 敏感字段全部透明加密”的结论只能标为部分完成。当前加密字段测试只覆盖 Project/SystemConfig。

### P1-13：两套取消 API 故障语义不一致，旧路径制造伪成功

新 `/runs/{run_id}/cancel` 在控制消息发送失败时返回 503；旧 [`/tasks/runs/{run_id}/stop`](../services/web_api/src/antcode_web_api/routes/v1/tasks.py#L1104) 在 [`_try_send_stop_event()`](../services/web_api/src/antcode_web_api/routes/v1/tasks.py#L1157) 失败后仍于 [`L1119`](../services/web_api/src/antcode_web_api/routes/v1/tasks.py#L1119) 把 TaskRun 标 CANCELLED 并返回成功。前端 [`tasks.ts`](../web/antcode-frontend/src/services/tasks.ts#L94) 仍保留 404 时回退旧路径。

Redis/Gateway 不可用、Worker 不存在或 P0-02 沙箱取消失败时，界面显示已取消，真实进程继续运行，后续成功结果又可能被终态吸收拒绝。

### P1-14：Crawl batch 配置多数没有进入真实执行链

CrawlBatch 暴露 `max_depth`、`max_pages`、`max_concurrency`、`request_delay`、`timeout`、`max_retries` 等配置，但 [`batch_dispatcher_service.py`](../packages/antcode_core/src/antcode_core/application/services/crawl/batch_dispatcher_service.py#L177) 只覆盖 `target_url`，其他行为继续使用 ProjectRule；timeout 还读取不存在的 `task_timeout` 并回退 3600 秒。

API 接受并保存的 batch 级配置因此呈现“已生效”但执行行为不变，属于明确功能契约错误。

### P1-15：Spider 数据默认不是完整、幂等的结果存储

Direct sink 和 Gateway handler 都以近似 `MAXLEN ~10000` 写 Redis Stream，并设置 24 小时 TTL；单 run 超过约 10000 条或过期后数据会消失。Gateway 批处理不是事务写，部分成功后异常时 sender 会恢复整批重发，而接收端没有按 `item_id` 去重，可能产生重复。

如果该 Stream 只是预览缓存，API 和文档必须明确；如果它是任务结果，当前保留和幂等语义不满足完整性要求。现有测试没有 >MAXLEN、TTL、部分成功重放和读端去重场景。

## 5. P2 明确问题

### P2-01：Access token 仍持久化在 localStorage，SPA 页面没有 CSP

[`api.ts`](../web/antcode-frontend/src/services/api.ts#L39) 和 [`auth.ts`](../web/antcode-frontend/src/services/auth.ts#L45) 读写 access token 到 `localStorage`。refresh token 改为 HttpOnly cookie 只解决了 refresh token 暴露；access token 仍可被 XSS、恶意扩展或第三方脚本读取。默认 token TTL 仍是 1440 分钟。

Web API 的安全头中间件只保护 API 响应，生产 [`Frontend Dockerfile`](../web/antcode-frontend/Dockerfile#L53) 返回 SPA HTML/JS 时未配置 CSP、HSTS、Permissions-Policy 等头。本轮未发现 `dangerouslySetInnerHTML` 等直接 sink，因此定为 P2 安全纵深问题，而不是现成 XSS 利用链。

### P2-02：WebSocket 全局统计对任意登录用户开放

[`websocket_logs.py`](../services/web_api/src/antcode_web_api/routes/v1/websocket_logs.py#L119) 的 `/stats` 只要求普通登录；相邻 `/cleanup` 明确要求管理员。返回内容包括全局连接数、active runs、丢弃消息、吞吐、字节数和 uptime，泄露集群运行元数据。

### P2-03：匿名 detailed health 暴露拓扑和底层错误

`GET /api/v1/health/detailed` 默认 `include_details=true` 且无认证。响应可包含 Worker name/host/port、熔断器状态、内存/磁盘和底层异常文本。应将匿名响应限制为 readiness 状态，把拓扑与错误详情放到管理员端点。

### P2-04：历史执行日志页只能打开最近 20 次执行

[`ExecutionLogs.tsx`](../web/antcode-frontend/src/pages/Tasks/ExecutionLogs.tsx#L85) 为打开一个 run，却请求任务 runs 默认第一页并本地查找；后端 [`tasks.py`](../services/web_api/src/antcode_web_api/routes/v1/tasks.py#L1015) 默认只返回 20 条。已有精确 `getTaskRun(runId)` 服务但未使用。第 21 次以前的历史链接会错误显示“未找到执行记录”。

### P2-05：子目录 artifact 可列出但无法下载，下载仍整块占内存

Worker artifact name 可以是 `reports/2026/result.json`，但 [`runs.py`](../services/web_api/src/antcode_web_api/routes/v1/runs.py#L232) 使用普通 `{artifact_name}` 单路径段，URL 解码后的斜杠导致路由不匹配。

下载端在 [`runs.py`](../services/web_api/src/antcode_web_api/routes/v1/runs.py#L251) 先 `read_blob()` 全量加载，再把内存 bytes 包成 StreamingResponse；底层 store 还会加载所有 chunks 后 join。并发大对象下载时内存按对象大小与并发数增长。

### P2-06：日志和 Spider 数据读取故障被伪装成空结果

[`runs.py`](../services/web_api/src/antcode_web_api/routes/v1/runs.py#L310) 捕获 Redis 异常后返回空 items；[`task_log_service.py`](../packages/antcode_core/src/antcode_core/application/services/logs/task_log_service.py#L90) 吞 PG 读取错误，再在 [`L115`](../packages/antcode_core/src/antcode_core/application/services/logs/task_log_service.py#L115) 吞 Redis 错误并返回空字符串。

数据库、Redis、schema 或网络故障最终表现为 HTTP 200“没有数据”，违反仓库的失败显式暴露规则，也使运维无法区分真实空结果与存储故障。

### P2-07：前端生产构建默认绕过自身 Nginx proxy

[`apiEndpoint.ts`](../web/antcode-frontend/src/utils/apiEndpoint.ts#L48) 在未显式配置时生成 `当前主机:8000` 的绝对地址；但 [`Frontend Dockerfile`](../web/antcode-frontend/Dockerfile#L53) 已配置同源 `/api` proxy。Compose 构建没有注入 `VITE_API_BASE_URL`。

浏览器因此直接访问宿主 8000 端口，而不是 Nginx proxy。只暴露 frontend、Web API 仅容器网络可达或页面启用 HTTPS 的标准部署会失败。

### P2-08：Gateway 毒任务直接 ACK，未进入已有 DLQ

[`poll.py`](../services/gateway/src/antcode_gateway/handlers/poll.py#L216) 对任何解析异常直接 XACK 丢弃；同文件 [`_requeue_task()`](../services/gateway/src/antcode_gateway/handlers/poll.py#L366) 已有 DLQ，却只用于 Worker 主动 reject。

损坏帧、协议不兼容或未来 schema 变化会永久删除任务，只靠 Master 180 秒后把 run 标 FAILED，没有保留原始帧供诊断和重放。

### P2-09：log ingest 的坏 Proto 永久留在 PEL

`log_ingest_loop` 没有像 `result_loop` 一样识别 typed envelope 的 `decode_error` 并在超过次数后 DLQ+ACK。坏帧会持续异常、留在 pending 并被重复 reclaim，形成常驻错误和消费阻塞风险。

### P2-10：outbox 发布窗口会重复，消费者未按 outbox_id 去重

Publisher 在数据库事务内先 XADD、再更新 `published_at`，崩溃窗口会重复发布；scheduler event consumer 虽收到稳定 `outbox_id`，却完全忽略。Crawl `batch_started` 的“已派发 URL 查询 -> TaskRun.create”之间也没有数据库唯一约束，重复事件可并发派发同一 seed。

### P2-11：安装 Key 明文存储，凭据文件权限设置存在窗口

[`worker_install_key.py`](../packages/antcode_core/src/antcode_core/domain/models/worker_install_key.py#L21) 明文存一次性 Key，数据库只读泄露可在 24 小时窗口抢注。Worker [`secrets.py`](../services/worker/src/antcode_worker/security/secrets.py#L172) 先 `write_text()` 再 chmod 0600，默认 umask 下存在短暂宽权限窗口，且 chmod 失败被忽略。

### P2-12：构建供应链仍使用可变下载和未校验脚本

Python 镜像 tag 未固定 digest；Dockerfile 使用 `curl .../uv/install.sh | sh`、`curl https://mise.run | sh`，grpc_health_probe 下载无 checksum；GitHub Actions 使用 tag 而非 commit SHA，部分工具使用 latest。SBOM、provenance、Trivy 和 Cosign 不能阻止构建阶段上游内容被替换。

### P2-13：Crawl items API 的“按 sequence 全局排序”契约不成立

读端按 TaskRun 顺序逐 run 读取，每个 run 的 sequence 又从 1 开始，结果不是跨 run 全局排序，也可能出现重复 sequence。多 run 交错场景没有测试。

### P2-14：删除链仍会留下日志、source snapshot 和 artifact 引用垃圾

手工 relation delete 不完整删除 `task_logs` 和 `RunSourceSnapshot`；任一 snapshot 又会阻止 artifact 清理。当前 orphan loop 只计数不修复。长期运行会导致关联垃圾和存储无界增长。

### P2-15：匿名/普通用户可见的本地用户状态可能长期陈旧

前端 [`auth.ts`](../web/antcode-frontend/src/services/auth.ts#L63) 明确没有 `/auth/me`，用户名、角色、禁用状态从 localStorage 读取。服务端授权仍是权威，因此不是直接越权，但 UI 菜单、角色展示和本地登录判断可能与服务端当前状态不一致。

## 6. P3 工程质量与门禁残余

严格扫描结果：

- `C901/PLR0911/PLR0912/PLR0913/PLR0915` 共 **202** 条，涉及 **75** 个文件。
- 其中 `C901=50`、`PLR0911=18`、`PLR0912=26`、`PLR0913=87`、`PLR0915=21`。
- Python/TypeScript/TSX/CSS 生产文件中，**145** 个超过 300 行，**69** 个超过 500 行。
- 代表性文件：`workers.py` 2207 行、`Monitor/index.tsx` 1773 行、`scheduler_loop.py` 1459 行、`engine.py` 1449 行、`RuleProjectForm.tsx` 1369 行。
- [`Monitor/index.tsx`](../web/antcode-frontend/src/pages/Monitor/index.tsx#L1) 仍有 `@ts-nocheck`，最大页面不受 TypeScript 门禁保护。
- [`pyproject.toml`](../pyproject.toml#L125) 对所有生产后端目录豁免 C901，PLR 未进入正式 select。
- [`mypy.ini`](../mypy.ini#L14) 仍允许无注解函数并使用 `follow_imports=silent`。

前端构建还产生约 1.22 MB 的 antd chunk、748 KB icons chunk和 2.1 MB 登录背景图。构建通过不代表首屏性能已闭环。

## 7. 自动化门禁结果

| 门禁 | 本轮结果 |
| --- | --- |
| Unit | `655 passed, 1 skipped`，14.38 秒，60 秒硬超时 |
| Boundary | `15 passed` |
| Contracts | `52 passed, 70 skipped` |
| Ruff | `ruff check .` 通过 |
| Ruff format | `575 files already formatted` |
| Mypy | 393 source files，0 errors |
| Bandit HIGH/HIGH | 0 |
| Frontend type-check | 通过 |
| Frontend ESLint | 0 warnings，通过 |
| Frontend build | 通过 |
| git diff --check | 通过 |
| Integration | 60 秒硬超时，无完成结果 |
| E2E | 60 秒硬超时，无完成结果 |
| npm audit | 首次因 DNS 失败；联网重试因依赖清单外发权限被拒，未验证 |

需要特别说明：70 个 contract skip 以及未完成的 integration/E2E 覆盖真实 PostgreSQL、Redis、Docker/bwrap、多副本、断网、切主和浏览器场景。当前绿色 unit/static 门禁不能替代这些生产路径验证。

## 8. 对 7 月 10 日报告的重新校准

| 旧结论 | 重新校准 |
| --- | --- |
| P0-05 Rule 子进程 Worker 级 token | 仍未关闭；默认配置下先表现为 Rule 不可用，手工放通后重新暴露全 Worker 凭据。 |
| P1-07 token/cookie | refresh token 已闭环；access token 仍在 localStorage，SPA 缺 CSP，应改为部分修复。 |
| P1-08 一次性凭据 | WebSocket ticket/Direct proof 已原子化；WorkerInstallKey 的数据库消费仍非原子。 |
| P1-10 敏感字段加密 | Project/SystemConfig 已覆盖；Task execution params/env 和 install key 仍明文。 |
| P1-11 Git SSRF | 初始 host/DNS 校验已覆盖；redirect/DNS rebinding 和连接目标固定仍未闭环。 |
| P1-12 状态一致性 | TaskRun runtime CAS 已改善；Task 同步仍有同 run 陈旧快照回退竞态。 |
| P1-14 outbox/幂等 Crawl | outbox 已存在；消费者未按 outbox_id 去重，dispatch 双失败仍会 ACK 事件。 |
| P1-15 fencing | 仍为残余，并新增确认 lease eviction 代际误杀和旧实例数据面写入窗口。 |
| P1-18 retry | durable queue 结构已存在；远程失败未接入，新 TaskRun 又重置 retry_count。 |
| P1-26 取消终止进程组 | ProcessExecutor 路径成立；生产 SandboxExecutor 路径不成立，应重新打开为 P0。 |
| P1-27 Spider sink | 失败恢复 buffer 已修；结果存储仍非完整、非幂等。 |
| P3 工程指标 | 未清零，正式配置仍依赖全生产目录豁免。 |

## 9. 排除项与评级校准

以下候选经复核不计入新漏洞：

- Gateway AuthInterceptor 已把认证主体与 payload Worker ID 绑定。
- Status、Logs、SpiderData 已校验 TaskRun 当前 DB Worker ownership；问题在于缺 lease/epoch 代际，而不是任意跨 Worker 写入。
- Web access token 每次请求会校验 UserSession、用户 active 和数据库当前角色；`AdminPermissionMiddleware` 只读 JWT claim 的缺陷没有在现有路由上形成独立授权绕过。
- WebSocket 已使用 Redis 一次性 ticket，连接期间会周期重校验用户和 session。
- 告警 httpx 默认不跟随重定向，因此 webhook redirect 不是当前直接利用链；DNS 解析与连接 TOCTOU 仍成立。

## 10. 发布判断

当前版本状态为：**拒绝生产发布**。

阻断原因不是测试数量不足，而是生产默认路径存在已复现的 P0：依赖构建在任务沙箱外执行，以及 SandboxExecutor 取消失效；同时 Rule 默认网络/认证链、任务 ACK、Lease 代际、远程 retry、Crawl 取消和多个 SSRF/敏感数据问题仍未闭环。

在这些问题关闭并通过真实 PostgreSQL/Redis、bwrap、Gateway、切主、断网和并发故障测试前，不应使用“全部修复完成”“生产就绪”或“最终验收通过”的结论。
