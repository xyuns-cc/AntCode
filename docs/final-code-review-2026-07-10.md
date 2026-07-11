# AntCode 最终代码审查与修复验收报告（2026-07-10）

## 1. 范围与结论

本报告合并并复核：

- [Codex 审查报告](code-review-2026-07-10.md)
- [Claude 审查报告](../审查报告-2026-07-10.md)

本轮已对 P0、P1、P2 问题实施修复，并重新执行后端、前端、类型、安全和供应链门禁。结论如下：

- 原报告中的绝大多数功能、安全、可靠性和发布链问题已修复。
- 常规 Ruff、格式、严格 mypy、后端测试、前端构建、Bandit、pip-audit、npm audit 均通过。
- 不能认定“全部修复完成”或“可直接生产发布”。仍有 1 个 P0 架构残余、1 个 P1 fencing 残余，以及未清零的 P3 代码指标。

状态说明：

- **已修复-A**：已有自动化测试或静态门禁验证。
- **已修复-I**：代码和定向测试已完成，但仍需真实 PostgreSQL、Redis、多副本、容器或 GitHub Actions 环境验收。
- **部分修复-R**：仍有明确残余，不得关闭问题。

## 2. 发布阻断项

### P0-05：Rule 子进程仍持有 Worker 级 Gateway token

真实沙箱、只读根、路径隔离、环境白名单和网络隔离已经接入，但 Gateway 模式的 Rule 子进程仍会得到 Worker 级 token：

- [rule/plugin.py](../services/worker/src/antcode_worker/plugins/rule/plugin.py) 将 `WORKER_GATEWAY_AUTH_TOKEN` 写入 `ANTCODE_SPIDER_GATEWAY_AUTH_TOKEN`。
- [sandbox.py](../services/worker/src/antcode_worker/executor/sandbox.py) 对 Rule 插件放行该秘密变量。
- [sinks/__init__.py](../packages/antcode_scrapy/src/antcode_scrapy/sinks/__init__.py) 和 [gateway_sink.py](../packages/antcode_scrapy/src/antcode_scrapy/sinks/gateway_sink.py) 在子进程中读取并发送该凭据。

这意味着被利用的 Rule 进程可在同一 Worker 身份范围内构造其他 run 的 Gateway 请求。Gateway 已增加 Worker/run ownership 校验，降低了攻击面，但凭据最小化仍未闭环。正确终态必须是以下之一：

1. Gateway 签发带 `aud=spider-data`、`run_id`、短 TTL 的 per-run capability token，并在服务端强制 scope。
2. 子进程只通过 Worker 本地 Unix socket/pipe 上报，Worker 主进程独占 Gateway 凭据。

因此 P0-05 状态为 **部分修复-R**，当前仓库仍不满足生产发布条件。

## 3. P0 验收矩阵

| ID | 最终状态 | 验收结论 |
| --- | --- | --- |
| P0-01 | 已修复-A | Gateway 只接受 Worker 专用凭据；JWT token class、认证主体、Worker、run ownership 已绑定，Status/Logs/SpiderData 均有回归测试。 |
| P0-02 | 已修复-A | Worker、Monitoring、Crawl 项目与批次 ACL 已接线，批量和详情路径覆盖授权测试。 |
| P0-03 | 已修复-A | Render 不再拼接 `python -c` 源码，路径和参数走结构化输入，安全集成测试通过。 |
| P0-04 | 已修复-A | runtime 名称和 resolved path 均限制在运行时根目录，越界删除测试通过。 |
| P0-05 | 部分修复-R | bwrap 沙箱已接入，但 Rule 子进程仍持 Worker 级 Gateway token，见第 2 节。 |
| P0-06 | 已修复-A | 通用用户更新禁止角色和他人密码字段；提权、重置、禁用只走超级管理员专用路径并记录审计。 |

## 4. P1 验收矩阵

| ID | 最终状态 | 修复摘要 |
| --- | --- | --- |
| P1-01 | 已修复-I | 新增 `task_logs` migration/model 初始化契约；需在目标 PostgreSQL 执行迁移验收。 |
| P1-02 | 已修复-I | Gateway 日志改为服务端持久化 ACK；Worker 等待 `LogAck/StatusAck`；需真实断网重连验收。 |
| P1-03 | 已修复-A | 历史日志 keyword-only 调用修正并加入契约测试。 |
| P1-04 | 已修复-A | 项目源码统一为 Git-only 契约，复制/更新移除旧字段和旧 service 签名。 |
| P1-05 | 已修复-I | Crawl 查询改为参数化底层连接和分页流式读取；需真实 JSONB 数据量验收。 |
| P1-06 | 已修复-A | Nginx/API/WS upstream、可信代理 IP、镜像 type-check/lint/build 已修复。 |
| P1-07 | 已修复-A | 删除密码持久化和长期 JWT URL fallback；refresh token 改为 HttpOnly、SameSite=Strict cookie。 |
| P1-08 | 已修复-A | WebSocket ticket 和 Direct 注册证明改为 Redis 原子消费。 |
| P1-09 | 已修复-A | 服务端 session/JTI、refresh rotation、重放检测、撤销和用户状态校验已接入。 |
| P1-10 | 已修复-I | Worker/API/Redis/告警/项目秘密改为哈希或透明加密，响应与日志脱敏；历史明文需运行迁移脚本。 |
| P1-11 | 已修复-I | Git SSRF、DNS 私网解析、timeout、大小/文件数/压缩比、artifact 到临时文件流式校验与解压已完成。 |
| P1-12 | 已修复-I | Task/TaskRun CAS、latest-run 锁、并发 claim 和终态吸收已实现；需并发数据库压测。 |
| P1-13 | 已修复-I | Artifact 元数据/chunk/引用/清理进入事务，读取改为有界临时文件；需真实大对象与故障注入。 |
| P1-14 | 已修复-I | Scheduler/Crawl 使用 PostgreSQL outbox、幂等派发和 seed 完整性；需切主/重启验收。 |
| P1-15 | 部分修复-R | grant/revoke/sweep 与 run ownership 已原子化并续租，ownership 使用 `worker_id:lease_id`；跨协议单调 fencing epoch 尚未贯穿。 |
| P1-16 | 已修复-I | Lease leader gate、Gateway ACK 映射、PEL 恢复和共享状态已迁移到 Redis；需多副本故障测试。 |
| P1-17 | 已修复-I | 分发后写 RUNNING/心跳，失租、超时和恢复扫描覆盖 dispatch/runtime 双状态。 |
| P1-18 | 已修复-I | retry/redispatch 持久化恢复并采用原子 claim/ACK/requeue；需 Master 崩溃点故障注入。 |
| P1-19 | 已修复-I | 解码失败保留 msg_id，DLQ 写失败不 ACK，Stream 使用 ACK-aware MINID 裁剪。 |
| P1-20 | 已修复-I | Worker 删除改为解绑任务和历史执行，关键删除进入事务；正式 FK 迁移需目标库验收。 |
| P1-21 | 已修复-A | Web API、Master、Gateway、Worker 均提供依赖感知 readiness，Docker/Compose 探针已切换。 |
| P1-22 | 已修复-A | Bandit、pip-audit 和 npm audit fail-closed；真实报告 schema 进入测试。 |
| P1-23 | 已修复-A | TaskRun 使用真实 task/run/worker 归属，自动选 Worker 后在入队前回写 `worker_id`。 |
| P1-24 | 已修复-A | Gateway/Worker codecs 与当前 protobuf 契约统一，Code/File/Rule 定向测试通过。 |
| P1-25 | 已修复-I | 发送、服务端持久化、任务 ACK 分阶段确认；失败保留 outbox/PEL。 |
| P1-26 | 已修复-A | 取消和缩容终止进程组并 wait，避免遗留子进程。 |
| P1-27 | 已修复-A | Spider sink 失败恢复原 batch，不再清空并假成功，后台定时 flush 已接入。 |
| P1-28 | 已修复-A | Crawl ID、分页、操作和导出契约统一，前端 type-check/build 通过。 |
| P1-29 | 已修复-A | 请求体、集合、嵌套 JSON、上传和 source bundle 均有显式边界。 |
| P1-30 | 已修复-A | Trigger 在持久化前完成 model-level 校验，失败不留下活跃任务。 |
| P1-31 | 已修复-A | Rule 表单 callback/initialData 稳定化，消除父子 effect 更新循环。 |
| P1-32 | 已修复-I | CrawlBatch 状态改为条件 CAS，避免覆盖 PAUSED/CANCELLED；需并发数据库验收。 |
| P1-33 | 已修复-A | MAINTENANCE 不再被心跳、检查或重注册覆盖。 |
| P1-34 | 已修复-I | leader/fencing/lease/control key 全部注入 Redis namespace；需双 namespace 共库验收。 |
| P1-35 | 已修复-I | Worker 镜像安装 Chromium 与运行依赖；需实际构建并运行浏览器 smoke test。 |

## 5. P2 验收矩阵

| ID | 最终状态 | 修复摘要 |
| --- | --- | --- |
| P2-01 | 已修复-I | 去重判定和提交改为 Redis 原子脚本。 |
| P2-02 | 已修复-A | Gateway spider sink 增加独立定时 flush 和准确确认计数。 |
| P2-03 | 已修复-A | 静态路由稳定提升到动态 `/{id}` 前，并用真实 Starlette 匹配测试固定。 |
| P2-04 | 已修复-A | WebSocket 历史游标归连接所有，连接计数、锁和队列生命周期统一。 |
| P2-05 | 已修复-A | 日志/WS/runtime 高基数状态增加背压、终态清理和 registry 回收。 |
| P2-06 | 已修复-I | Worker HMAC secret 从数据库读取；nonce、Web/Gateway 限流使用 Redis 权威状态，失败不回退本地。 |
| P2-07 | 已修复-I | Crawl JSONB 查询、活跃 run、render-capable 过滤/count/offset/limit 下推数据库。 |
| P2-08 | 已修复-I | HALF_OPEN permit 释放和数据库计数更新改为原子操作。 |
| P2-09 | 已修复-I | Crawl 队列、进度和 dedup 按 batch 隔离。 |
| P2-10 | 已修复-A | 包名、限流接口和严格 Sentinel URL/password/db 解析修复，非法配置显式失败。 |
| P2-11 | 已修复-A | UVManager timeout 终止进程组并等待回收。 |
| P2-12 | 已修复-A | JsonCodec 先处理 bool，并增加 round-trip 契约。 |
| P2-13 | 已修复-A | Direct 批量日志任一分组失败返回 HTTP 503。 |
| P2-14 | 已修复-A | 旧 Crawl queue 兼容路径删除或统一到当前 dispatcher。 |
| P2-15 | 已修复-A | running tasks、用户下线、Crawl 认证导出等前后端契约修复。 |
| P2-16 | 已修复-A | task import 有界读取、严格 UTF-8，缺 PyYAML 明确报错。 |
| P2-17 | 已修复-I | RSA 读取失败阻断；SecretBox 使用显式随机 salt，legacy SHA256 仅迁移开关启用。 |
| P2-18 | 已修复-I | Direct reclaim 适配当前 redis-py，广播/确认语义修复。 |
| P2-19 | 已修复-A | Dashboard/Monitoring ACL 和 refresh 权限统一。 |
| P2-20 | 已修复-A | Gateway 停机使用真实 worker_id Deregister。 |
| P2-21 | 已修复-I | Crawl 单 run 分页 generator，达到全局 limit 立即停止，错误显式上抛。 |
| P2-22 | 已修复-A | Dashboard 一轮刷新复用同一 metrics 结果。 |
| P2-23 | 已修复-A | Master/Gateway 初始化显式传 service，专用 DB pool 配置生效。 |
| P2-24 | 已修复-I | control producer 统一，按消费确认下界 MINID 裁剪。 |
| P2-25 | 已修复-A | semaphore 覆盖完整后台执行，单请求和队列有界，shutdown drain 在途任务。 |
| P2-26 | 已修复-A | 文档/API/规则抽取契约统一，未实现 JSONPath 在 schema 边界拒绝。 |

## 6. 发布供应链验收

已完成：

- Docker 发布改为由 CI 全门禁成功后调用。
- 候选本地镜像先做 Trivy HIGH/CRITICAL 阻断扫描。
- 推送后按最终 digest 再扫描。
- BuildKit 开启 SBOM 与 provenance。
- 最终 digest 使用 keyless Cosign 签名。
- 删除 `latest` 标签，只发布 SHA/semver 标签。
- 手工发布要求当前 commit 已有成功 CI。
- Web API Dockerfile/Compose 健康检查改为 `/api/v1/health/ready`。

本地已通过 YAML 解析和供应链契约测试；GitHub OIDC、GHCR 推送、多架构构建、Trivy 远端 digest 和 Cosign 需要在真实 GitHub Actions 环境执行。

## 7. 最终自动化结果

| 门禁 | 结果 |
| --- | --- |
| Unit | `652 passed, 1 skipped`，15.16 秒，60 秒硬超时 |
| Boundary | `15 passed` |
| Contracts | `52 passed, 70 skipped` |
| Ruff | `ruff check .` 通过 |
| Ruff format | 575 文件通过 |
| Mypy | 393 source files，`check_untyped_defs=True`，0 errors |
| Frontend type-check | 通过 |
| Frontend lint | 0 warnings，通过 |
| Frontend build | Vite 7.3.6 生产构建通过，无循环 chunk/超限警告 |
| Bandit HIGH/HIGH | 0 |
| pip-audit | No known vulnerabilities found |
| npm audit | 0 vulnerabilities |
| git diff --check | 通过 |

`contracts` 的 70 个 skip 主要依赖真实 PostgreSQL、Redis、Docker/bwrap 或外部服务，不能用本地 unit 结果替代集成验收。

## 8. P3 工程质量残余

本轮已经拆分 Web API、Gateway、Core 的多批复杂函数，并把严格 mypy 从约 80 条降为 0；`source_bundle_service.py`、`worker_connection_service.py`、Redis factory 等已降到 300 行以内。

但全仓硬指标仍未满足：

- 隔离 `C901` 扫描仍有 **50** 个复杂函数。
- 隔离 `C901/PLR0911/PLR0912/PLR0913/PLR0915` 扫描仍有 **201** 条。
- `workers.py` 2207 行、`scheduler_loop.py` 1459 行、`engine.py` 1449 行。
- 多个前端页面/组件仍超过 1000 行。
- Ruff 正式配置仍对生产目录豁免 C901，PLR 尚不能全局启用，否则 CI 会出现上述存量失败。

因此 P3 只能标记为“已开始系统性修复，未清零”。最终发布判断保持：**P0-05、P1-15 和 P3 硬指标关闭前，不应宣称全部修复或直接生产发布。**
