# AntCode 深度代码审查报告（2026-07-10）

## 1. 审查概况

- 审查范围：Web API、Master、Gateway、Worker、Core、Redis/PostgreSQL、前端、Docker、CI 与测试配置。
- 审查方式：静态代码检查、路由匹配验证、Python 编译、前端构建与类型检查、后端测试收集、边界测试、契约测试、Bandit、mypy 和 Docker Compose 配置验证。
- 严重度：P0 表示可造成跨租户或跨 Worker 篡改；P1 表示高风险安全、数据丢失或生产不可用；P2 表示明确功能、性能或资源问题；P3 表示工程质量与维护风险。
- 结论：当前存在 3 组关键越权漏洞，以及日志持久化、部署链路、状态一致性和质量门禁失效等问题。生产部署前应优先关闭身份绑定和资源授权缺口。

## 2. P0：关键安全问题

### 2.1 Worker 身份未绑定任务归属

Worker 通过认证后，状态、日志和心跳接口直接使用请求中的 `run_id`，没有确认任务是否分配给当前 Worker。Master 消费结果时也没有使用 `TaskStatus.worker_id` 校验消息来源。Direct 模式 Redis ACL 允许所有 Worker 写共享结果和日志流。

影响：任意持有有效 Worker 凭据的节点都可能伪造其他 Worker、项目或租户的日志与执行结果。

证据：

- [`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L1705)
- [`result_loop.py`](../services/master/src/antcode_master/ingester/result_loop.py#L212)
- [`redis_acl.py`](../packages/antcode_core/src/antcode_core/common/security/redis_acl.py#L36)

修复方向：根据认证上下文获得可信 Worker ID，并在接收日志、心跳、状态和结果前，原子校验 `run_id` 的当前分配记录；Master 消费结果时执行同一校验。Direct 模式应使用 Worker 独立 stream 或服务端代理写入。

### 2.2 Gateway 认证身份未绑定 RPC payload

Gateway 拦截器只验证 metadata 中的 Worker ID，但 `StreamTasks`、状态流、日志流和抓取数据流继续信任消息体里的 `worker_id` 与 `run_id`。

影响：有效 Worker 可以订阅其他 Worker 的任务队列、窃取任务，或提交伪造状态、日志和抓取数据。

证据：

- [`auth.py`](../services/gateway/src/antcode_gateway/auth.py#L145)
- [`data_service.py`](../services/gateway/src/antcode_gateway/services/data_service.py#L67)

修复方向：认证拦截器应把可信身份放入 RPC context；各服务只使用 context 身份，并拒绝 payload 身份不一致的请求。所有 `run_id` 操作还需验证当前任务分配关系。

### 2.3 普通用户可跨项目操作 Worker 和任务

多个接口只要求用户已登录，没有校验 Worker、队列任务、项目或 `run_id` 的所有权。普通用户可以断开任意 Worker、取消或修改任意排队任务、批量分发其他项目任务，并读取其他任务的状态与日志。

证据：

- [`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L553)
- [`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L1433)
- [`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L1506)
- [`worker_dispatcher.py`](../packages/antcode_core/src/antcode_core/application/services/workers/worker_dispatcher.py#L710)

修复方向：建立统一资源授权服务，对 Worker、Project、Task、TaskRun 和 QueueItem 执行所有权或管理员权限检查；批量操作必须逐项授权，不能只校验请求本身。

## 3. P1：高风险问题

### 3.1 新部署不会创建 `task_logs` 表

历史日志服务依赖 `task_logs`，但没有对应 Tortoise model，迁移目录也没有代码中声称存在的迁移。初始化脚本仅调用 `generate_schemas(safe=True)`。读取失败后异常被吞掉并返回空列表，数据库故障会伪装成“没有历史日志”。

证据：[`init_db.py`](../scripts/init_db.py#L74)、[`postgres_log_service.py`](../packages/antcode_core/src/antcode_core/application/services/logs/postgres_log_service.py#L45)、[`postgres_log_service.py`](../packages/antcode_core/src/antcode_core/application/services/logs/postgres_log_service.py#L130)。

修复方向：为日志表提供正式 model 和可重复迁移；启动时验证必要表与索引；查询失败应抛出明确的持久化错误并记录完整上下文。

### 3.2 前端长期保存可逆密码，并可能退化为明文

密码“加密”基于设备指纹、简单 hash 和 XOR，不具备密码学安全性。加密异常时直接返回原始密码，登录页随后将结果长期写入 `localStorage`。

证据：[`crypto.ts`](../web/antcode-frontend/src/utils/crypto.ts#L7)、[`crypto.ts`](../web/antcode-frontend/src/utils/crypto.ts#L59)、[`Login/index.tsx`](../web/antcode-frontend/src/pages/Login/index.tsx#L67)。

修复方向：删除密码持久化功能。若需要“记住登录”，使用服务端可撤销、短期访问令牌配合 HttpOnly、Secure、SameSite cookie，不保存用户密码。

### 3.3 生产前端容器代理配置错误

Compose 中后端服务名是 `web-api`，Nginx 却代理到不存在的 `backend:8000`。实际 WebSocket 路径会进入普通 `/api` location，缺少 Upgrade 头。Docker 构建还显式跳过 TypeScript 检查。

证据：[`docker-compose.dev.yml`](../infra/docker/docker-compose.dev.yml#L197)、[`Dockerfile`](../web/antcode-frontend/Dockerfile#L23)、[`Dockerfile`](../web/antcode-frontend/Dockerfile#L50)。

修复方向：统一 upstream 服务名，为 `/api/v1/ws/` 配置 WebSocket 代理头，并把 `type-check` 作为镜像构建的强制步骤。

### 3.4 一次性凭据存在并发重放

WebSocket ticket 和 Direct 注册证明均采用 Redis `GET` 后 `DELETE`，两个并发请求可以在删除前读到同一凭据。

证据：[`websocket_logs.py`](../services/web_api/src/antcode_web_api/routes/v1/websocket_logs.py#L79)、[`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L944)。

修复方向：使用 Redis `GETDEL` 或 Lua 脚本原子消费凭据，并保留明确的过期与重放错误。

### 3.5 长期 JWT 暴露在 URL 和浏览器存储中

ticket 获取失败时，前端回退为将长期 JWT 放入 WebSocket URL；旧 WebSocket 服务也继续使用 `?token=`。默认 JWT 有效期为 1440 分钟并存储于 `localStorage`，但前端注释声称为 15 分钟。

证据：[`logs.ts`](../web/antcode-frontend/src/services/logs.ts#L187)、[`websocket.ts`](../web/antcode-frontend/src/services/websocket.ts#L101)、[`config.py`](../packages/antcode_core/src/antcode_core/common/config.py#L120)、[`api.ts`](../web/antcode-frontend/src/services/api.ts#L35)。

修复方向：ticket 获取失败应显式失败，不能回退到长期令牌；统一令牌有效期和前端说明，并减少可被脚本读取的长期凭据。

### 3.6 用户禁用或降权不会立即生效

通用认证只信任 JWT claim，不查询当前用户状态。Web API 的另一套依赖虽然查询数据库，但没有检查 `is_active`。

证据：[`auth.py`](../packages/antcode_core/src/antcode_core/common/security/auth.py#L170)、[`deps.py`](../services/web_api/src/antcode_web_api/deps.py#L23)。

修复方向：收敛为一套认证依赖，在授权前读取当前用户状态和角色版本；禁用用户、密码变更或角色调整时使旧会话失效。

### 3.7 告警敏感配置明文存储并原样返回

Webhook、SMTP 密码等敏感值明文写入 `system_configs`，读取接口又将其返回给管理员前端，扩大数据库和管理会话泄露的影响。

证据：[`alert.py`](../services/web_api/src/antcode_web_api/routes/v1/alert.py#L34)、[`alert.py`](../services/web_api/src/antcode_web_api/routes/v1/alert.py#L169)、[`system_config.py`](../packages/antcode_core/src/antcode_core/domain/models/system_config.py#L18)。

修复方向：使用密钥管理或应用层信封加密，读取接口只返回“已配置”状态和掩码；更新时使用单独的 write-only 字段。

### 3.8 Git 拉取存在 SSRF 面且可能无限挂起

源码 URL 只校验 scheme，没有限制回环、链路本地、内网或云元数据地址；`git clone` 和 `ls-remote` 没有 timeout。

证据：[`source_bundle_service.py`](../packages/antcode_core/src/antcode_core/application/services/projects/source_bundle_service.py#L38)、[`source_bundle_service.py`](../packages/antcode_core/src/antcode_core/application/services/projects/source_bundle_service.py#L137)。

修复方向：在服务边界执行目标地址解析和网络策略校验，为 Git 子进程设置硬超时并明确传播超时错误。

### 3.9 Source bundle 缺少资源边界

源码包在构建、数据库写入、读取和解压阶段均全量驻留内存，没有总大小、文件数、单文件大小或压缩比限制，存在内存耗尽与 zip bomb 风险。

证据：[`source_bundle_paths.py`](../packages/antcode_core/src/antcode_core/application/services/projects/source_bundle_paths.py#L46)、[`artifact_store.py`](../packages/antcode_core/src/antcode_core/infrastructure/postgres/artifact_store.py#L26)、[`fetcher.py`](../services/worker/src/antcode_worker/projects/fetcher.py#L119)。

修复方向：采用流式生成和读取，在入口与解压阶段实施显式、可配置并有文档的资源限制，同时拒绝路径穿越和符号链接逃逸。

## 4. P2：数据一致性和功能问题

### 4.1 丢失执行结果仍会 ACK

`TaskRunService.update_result` 找不到执行记录时返回 `True`，Master 随后 ACK 消息，结果将永久丢失。

证据：[`task_run_service.py`](../packages/antcode_core/src/antcode_core/application/services/task_run_service.py#L61)。

修复方向：不存在的 `run_id` 必须返回明确失败并进入可观测的异常处理路径，不能确认消费成功。

### 4.2 状态机更新存在并发覆盖

状态更新采用 read-check-save，没有事务、行锁或 compare-and-set。并发终态可能相互覆盖；reconcile 还有两条路径绕过状态机直接写 `status`。

证据：[`execution_status_service.py`](../packages/antcode_core/src/antcode_core/application/services/scheduler/execution_status_service.py#L132)、[`reconcile_loop.py`](../services/master/src/antcode_master/control/reconcile_loop.py#L189)。

修复方向：通过带期望旧状态的条件更新实现原子状态迁移，所有写入路径统一进入状态服务。

### 4.3 Artifact 写入与清理事务不完整

Artifact 元数据和 chunks 不在同一事务中。并发写入相同 hash 会触发唯一键竞态，chunk 失败会留下损坏记录。清理逻辑虽然进入事务上下文，却使用事务外连接。

证据：[`artifact_store.py`](../packages/antcode_core/src/antcode_core/infrastructure/postgres/artifact_store.py#L26)、[`artifact_cleanup_service.py`](../packages/antcode_core/src/antcode_core/application/services/projects/artifact_cleanup_service.py#L78)。

修复方向：元数据和 chunk 在同一事务连接中写入，使用数据库 upsert 处理相同 hash 竞争；读取前校验完整性。

### 4.4 固定 Worker 路由被动态路由遮蔽

`GET /{worker_id}` 注册早于 `/best` 和 `/render-capable`。已通过 Starlette matcher 验证，请求固定路径时会先进入 Worker 详情路由。

证据：[`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L569)、[`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L1624)。

修复方向：固定路由必须在动态路由前注册，或者为动态路径增加不会与保留词冲突的前缀。

### 4.5 第二个日志客户端可能收不到历史日志

Redis 日志订阅的 `history_sent` 按 run 共享，而不是按连接维护。首个客户端连接后，后续客户端可能只收到实时部分。

证据：[`redis_log_stream_service.py`](../services/web_api/src/antcode_web_api/websockets/redis_log_stream_service.py#L75)。

修复方向：历史游标和已发送状态必须属于订阅连接，不能属于共享 run 状态。

### 4.6 Worker 自适应限流实际失效

代码导入不存在的 `get_system_metrics`，随后吞掉 `ImportError`，导致负载保护无明显错误地永久关闭。

证据：[`resilience.py`](../services/worker/src/antcode_worker/services/resilience.py#L316)。

修复方向：修正指标接口并让初始化失败显式终止对应能力启动，不能把导入错误当作正常无指标状态。

## 5. P2：性能与资源管理问题

### 5.1 WebSocket 连接管理泄漏和竞争

超出单 run 上限时只关闭旧连接而不删除字典项；全局上限检查不是原子的；调用方忽略 `add_connection=False`；run 级锁从不清理。长期运行会造成内存增长、连接统计失真和上限绕过。

证据：[`websocket_connection_manager.py`](../services/web_api/src/antcode_web_api/websockets/websocket_connection_manager.py#L68)、[`websocket_connection_manager.py`](../services/web_api/src/antcode_web_api/websockets/websocket_connection_manager.py#L88)、[`websocket_connection_manager.py`](../services/web_api/src/antcode_web_api/websockets/websocket_connection_manager.py#L421)。

### 5.2 分布式日志缓存没有生命周期

`DistributedLogService` 为每个 run 保留缓存、sequence 和状态。虽然定义了 `clear_cache`，项目中没有调用点，run 数量增长会持续占用内存。

证据：[`distributed_log_service.py`](../packages/antcode_core/src/antcode_core/application/services/workers/distributed_log_service.py#L26)。

### 5.3 多个查询路径随全表数据线性退化

- Crawl batch 幂等检查扫描整个 `TaskRun` 表并在 Python 中解析 JSON：[`batch_dispatcher_service.py`](../packages/antcode_core/src/antcode_core/application/services/crawl/batch_dispatcher_service.py#L264)。
- Worker 历史和聚合接口全量读取后在 Python 中计算：[`worker_stats_service.py`](../packages/antcode_core/src/antcode_core/application/services/workers/worker_stats_service.py#L15)。
- render-capable 先加载全部在线 Worker 再内存分页：[`workers.py`](../services/web_api/src/antcode_web_api/routes/v1/workers.py#L1679)。

修复方向：将幂等键结构化为带唯一索引的字段；统计使用数据库聚合；过滤和分页必须在查询层完成。

## 6. P3：复杂度和可维护性

### 6.1 文件规模严重超过项目约束

扫描发现 137 个生产文件超过 300 行，60 个超过 500 行。`workers.py` 达 2105 行，`scheduler_loop.py` 达 1489 行，多个前端页面超过 1000 行。路由、授权、查询、序列化和状态变更集中在同一文件，增加回归和并发缺陷风险。

建议按资源和用例拆分路由服务，将授权、状态迁移、查询和传输适配器分离；拆分时保留对外契约，避免把大文件机械分割成相互强耦合的小文件。

### 6.2 复杂度门禁实际失效

Ruff 对全部生产目录豁免 `C901`；配置注释声称启用 PLR，实际 `select` 中没有 PLR。Ruff 本身也未声明在开发依赖中，Makefile 和 CI 命令无法从项目声明环境复现。

证据：[`pyproject.toml`](../pyproject.toml#L100)。

### 6.3 测试和 CI 无法形成回归保障

`.gitignore` 忽略绝大多数测试目录，Git 当前只跟踪少量 contract 测试。CI 注释明确不运行后端测试。单元测试当前又在收集阶段因 9 个过期导入失败。

证据：[`.gitignore`](../.gitignore#L106)、[`ci.yml`](../.github/workflows/ci.yml#L20)。

### 6.4 类型检查没有可执行基线

mypy 没有适合当前代码的渐进式配置，扫描产生数百条错误；前端类型检查存在 7 个真实错误。因此类型检查当前既不能作为合并门禁，也无法稳定揭示新增回归。

前端错误集中于：

- `RuleProjectForm.tsx` 的表单值和组件属性类型。
- `BatchList.tsx` 的数据结构类型。
- `ProjectDetailCards.tsx` 的字段访问和组件参数。
- `services/projects.ts` 的返回值类型。

## 7. 验证结果

| 检查项 | 结果 |
| --- | --- |
| Python 编译检查 | 通过 |
| 前端 `type-check` | 失败，7 个 TypeScript 错误 |
| 前端 `build` | 失败，被相同类型错误阻断 |
| 前端 `lint:ci` | 失败，2 个 warning |
| 后端 unit tests | 收集失败，9 个过期或不存在的导入 |
| Boundary tests | 2 failed，13 passed |
| Contract tests | 49 passed，70 skipped |
| Docker Compose 配置解析 | 通过；缺少密码时仅警告并使用空值 |
| Bandit | 完成；存在大量吞异常告警及若干中高风险项 |
| mypy | 失败，存在数百条错误 |
| Ruff | 无法运行，开发依赖未声明 Ruff |
| `npm audit` | 当前环境 DNS/网络受限，未完成 |

未运行集成测试与 E2E。当前单元测试收集失败、前端构建失败，因此仓库尚不具备可信的全量回归基线。

## 8. 修复优先级

1. 绑定 Worker 认证身份、任务分配和所有上报接口，修复 Gateway payload 身份越权。
2. 为 Worker、队列、项目、任务和日志接口补齐统一资源授权。
3. 修复 `task_logs` schema、生产 Nginx/WebSocket 配置和执行结果 ACK 语义。
4. 删除密码持久化和 JWT URL 回退，原子消费一次性凭据，加密敏感系统配置。
5. 统一事务状态机和 Artifact 事务边界，修复 WebSocket、日志缓存生命周期。
6. 恢复前后端构建、类型检查和测试收集，再将这些检查设置为 CI 强制门禁。
