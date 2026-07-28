# AntCode 第六轮修复后复审报告（非 K8s，2026-07-23）

> 复审对象：`867cbab` 及当前未提交工作树。
>
> 工作树状态：288 个 tracked 修改、1 个 tracked 删除、484 个 untracked 文件，共 773 个状态项。
>
> 本轮性质：只读代码审查与本地自动化验证；未修改业务代码，未连接测试机，未执行真实 PostgreSQL/Redis 全链路、浏览器 E2E 或压力测试。

## 1. 范围与 K8s 决策

项目已明确决定不采用 Kubernetes。本报告因此：

- 不把 `infra/k8s/`、Kustomize、NetworkPolicy、K8s Secret/探针/存储等问题计入当前发布门禁；
- 将第五轮报告中的 K8s 问题标记为“按产品决策移出范围”，而不是“代码已修复”；
- 以 Docker/Compose、进程部署、反向代理/TLS、中间件、备份恢复和 Worker 隔离作为非 K8s 生产形态的验收对象；
- 不要求执行 K8s 渲染或集群测试。

## 2. 执行结论

当前版本仍然**不能生产发布，也不应开始压测**。

排除 K8s 后仍有四个独立 P0：

1. `HEAD` 不包含当前业务实现所依赖的关键文件，干净 checkout 无法启动 Master、Gateway、Worker。
2. 前端 production build、Ruff 和 mypy 确定失败；后端绿灯还包含文件级 skip 隐藏的 8 个真实失败。
3. 仓库没有可执行的非 K8s 生产部署画像；现有 Compose 被代码和文档明确限定为开发/远程验收配置。
4. Worker 沙箱不能保护 Worker 身份和中间件凭据，且主日志链可直接外带这些数据。

第五轮剩余的 Lease/结算、状态机、跨存储事务、日志/SSE 容量和前端功能问题没有业务提交修复。最新提交只改动复杂度基线、删除一个边界测试并整体 skip 一个单元测试文件，不能关闭这些问题。

## 3. 本轮确认关闭或移出范围

- K8s 全部问题：**移出范围**，原因是部署路线改变，不是实现修复。
- 重复 `gateway.proto`、旧 Gateway 单体服务、旧 project storage/version 路由、13 个旧 Aerich/MySQL Python migration 已从当前工作树删除。
- 旧 log storage/archiver 的 Bandit MD5 问题已随旧实现删除；Bandit High/High 当前为 0。
- `pip-audit` 扫描 149 个锁定依赖无已知漏洞；官方 npm registry audit 为 0 vulnerabilities。
- migration inventory、Ruff format、`git diff --check`、三套已明确命名的 Compose 组合解析通过。
- 后端五个单元分片的直接断言当前通过；但 Core 的 9 个 skip 不可接受，见 P1-TEST-01。

## 4. P0 生产阻断

### P0-01 发布提交闭包不完整

以下生产文件存在于工作树，但不属于 `HEAD`：

- `services/master/src/antcode_master/control/result_metadata.py`
- `packages/antcode_core/src/antcode_core/application/services/crawl/spider_storage_cleanup.py`
- `services/worker/src/antcode_worker/executor/rule_policy.py`
- `packages/antcode_core/src/antcode_core/common/security/network_source.py`
- `packages/antcode_core/src/antcode_core/infrastructure/redis/url_security.py`
- `services/worker/src/antcode_worker/executor/artifact_collector.py`
- `contracts/proto/artifact.proto`
- `scripts/__init__.py`

用 `git archive HEAD` 构造干净源码快照并使用当前虚拟环境导入：

| 服务 | 结果 |
|---|---|
| Web API | 基础 import 通过 |
| Master | 缺 `antcode_master.control.result_metadata` |
| Gateway | 缺 `antcode_core.application.services.crawl.spider_storage_cleanup` |
| Worker | 缺 `antcode_worker.executor.rule_policy` |

`infra/docker/Dockerfile.web_api:89` 还会 `COPY scripts/__init__.py`，干净 `HEAD` 不含该文件，镜像构建会在 COPY 阶段失败。当前目录可导入不代表提交可重建，发布对象必须以干净 checkout 为准。

### P0-02 发布质量门禁失败

本轮复跑结果：

| 检查 | 当前结果 | 判定 |
|---|---|---|
| 前端 type-check | `Logs/index.tsx` 3 个 TS2339 | 失败 |
| 前端 lint:ci | `Cookies/index.tsx:148` 1 个 warning，`max-warnings=0` | 失败 |
| 前端 production build | 被相同 TypeScript 错误阻断 | 失败 |
| 前端 Vitest | 19 files / 96 tests passed | 通过，但不能抵消构建失败 |
| Ruff | 8 errors | 失败 |
| mypy | 100 errors / 23 files / 570 source files | 失败 |
| 严格复杂度命令 | 通过，带 972 条 baseline findings | 仅增量基线通过 |

`web/antcode-frontend/src/pages/Logs/index.tsx:48,51,89` 使用不存在的 `execution_id`、`LogService.getAllLogs` 和 `LogService.clearLogs`；生产 Dockerfile 必须先执行 type-check，因此前端镜像不能构建。该页面本身还是 untracked，说明前端发布集合也未确定。

Ruff 的 8 个错误来自 untracked SpiderKit 和两个 integration 文件；mypy 同时暴露未完成 SpiderKit、旧 websocket、memory queue、worker project 等实现。不能以“都在 untracked 文件”为理由忽略，因为当前运行代码和测试正在引用同一批 untracked 生产模块。

### P0-03 不存在非 K8s 生产部署画像

仓库只有：

- `infra/docker/docker-compose.dev.yml`
- `infra/docker/docker-compose.remote.yml`
- `infra/docker/docker-compose.remote.gateway.yml`

`infra/docker/README.md:110-138` 明确把 dev/remote 定义为开发/远程验收画像，并禁止用于承接不可信生产任务。remote 配置还固定使用：

- 明文 HTTP 和 `AUTH_COOKIE_SECURE=false`；
- `REDIS_ACL_ENABLED=false`；
- `ANTCODE_GATEWAY_ALLOW_INSECURE=true`；
- Gateway Worker 覆盖中的 `WORKER_GATEWAY_TLS=false`；
- Direct Worker 的数据库/Redis 连接信息；
- `SYS_ADMIN` 和 `seccomp/apparmor/systempaths=unconfined`。

仓库没有对应的生产 Compose、TLS 反向代理、证书轮换、生产 Secret 注入、Redis ACL、数据库迁移编排、持久化备份/恢复、滚动升级和回滚定义。`Makefile:152-160` 在 `infra/docker` 中执行裸 `docker compose`，但该目录没有默认 `compose.yml`；实测 `docker compose config` 返回 `no configuration file provided`。

因此，停用 K8s 后需要的是一条新的非 K8s 生产部署合同，不能把 remote 验收画像改名后直接上线。

### P0-04 Worker 沙箱与身份隔离失效

- `executor/sandbox.py:253-254` 对任何名为非 `bwrap` 的绝对命令直接前缀执行，配置可绕过实际沙箱。
- `executor/sandbox.py:266-268` 仍以 `--ro-bind / /` 暴露容器根文件系统；遮蔽只覆盖已知目录，不能覆盖单文件凭据、自定义挂载或 Worker 配置。
- `config.py:436` 将 Direct Redis URL写回 Worker 配置；`0600` 也不能隔离同 UID 的不可信任务。
- `executor/process.py:301` 记录完整 argv；`process.py:567-583` 将原始 stdout/stderr 直接写入日志 sink，绕过 `LogStreamer` 的脱敏路径。
- 终止进程失败仍可能只记录异常，调用方继续报告取消/自隔离成功，旧进程可与接管者并行执行。
- `services/worker/runtime_data/secrets/worker_credentials.json` 当前为 untracked、未被 ignore、权限 `0644`，且包含非空运行时身份字段。

在此边界下，恶意或被攻破的任务可以读取身份/中间件信息，再通过 stdout、Redis、HTTP 或任务网络外带。该问题与是否使用 K8s 无关。

## 5. P1 高风险正确性与安全问题

### 5.1 Gateway、Lease 与 Direct Redis

| ID | 未关闭问题 | 影响 |
|---|---|---|
| P1-GW-01 | `granted_at_ms` 取整到毫秒，renew 也重写；PG CAS 接受 `stored_gen <= new_gen` | 同毫秒 L1/L2 代际相等，迟到旧 Lease 仍可覆盖 |
| P1-GW-02 | ownership claim 不检查 TaskRun 终态，终态落库后 ACK 丢失仍可接管 | 已完成 run 重复执行 |
| P1-GW-03 | Direct L2 claim 未接线 PG Lease generation bind | 副作用已发生但状态/日志被旧 PG 绑定拒绝 |
| P1-GW-04 | Lease check 与 XADD/EVAL/XACK 分离，仍有 TOCTOU | 切代可插入两步之间，旧 Worker 继续提交或 ACK |
| P1-GW-05 | cancel/kill 失败可能仍报告成功 | 旧子进程与 L2 双执行 |
| P1-GW-06 | settlement legacy 默认/非法配置 fail-open | 兼容通道长期绕过严格 fence |
| P1-DR-01 | ACL 必须允许共享结果/日志、ownership key 和全局 group 的底层命令 | 被攻破 Worker 可伪造、吞消息或破坏 ownership |
| P1-DR-02 | Redis `SETUSER` 与 PG save 跨存储，无行锁/version CAS | 并发轮换可产生 Redis/PG 凭据分裂 |

`common/security/redis_acl.py:11-40` 已在源码注释中明确承认 ACL 无法表达 run/group/成员级所有权。删除 Worker 后旧 mTLS 证书仍可重新取得 Lease；`GRPC_HOST` 配置没有形成可靠监听合同；prefetch 缺总量上限；CancelTask/UpdateConfig 缺 Lease fence。

### 5.2 状态机、消息结算与数据事务

- batch/stop/crawl cancel 仍根据陈旧快照决定是否发送 control；快照后新建或新绑定 run 可继续执行。
- dequeue 后 `remove=False`、`CANCELLED -> PREPARING` 冲突和 tombstone 生命周期没有形成 durable fence。
- Redis `MULTI/EXEC` 重试可重放 XADD；有限窗口尾扫不能证明全局幂等，FAILED 重派也未原子复位状态。
- Lease/Worker 校验仍在状态事务外；事务内语义冲突返回值可能提交此前更新。
- 新消息优先会让 PEL 在持续流量下饥饿；部分恢复循环没有 leader gate、稳定分页或 durable retry intent。
- standby Master 可消费触发事件；只写入本机 scheduler 就标 outbox consumed，进程崩溃可永久丢触发。
- Artifact cleanup 的 statement snapshot 看不到等待锁期间新引用；也不识别 `TaskRun.result_data` 引用。
- Task/Project 删除在提交后 purge 日志和 Redis 数据；崩溃后无 durable cleanup，late writer 还能重建孤儿。
- HTTP batch logs 分组独立提交却整体返回 503，缺稳定 event ID，调用方重试会复制已成功组。
- outbox 达到重试上限仍写 `consumed_at`；takeover/ACK 交错可留下已 ACK 但永不 consumed 的事件。
- Gateway batch 使用 `task_id=0`，SpiderData ownership 强制查真实 Task，批次数据上报合同冲突。

### 5.3 日志、SSE、容量与资源边界

- HTTP 日志允许一次物化 10,000 行、单行接近 1 MiB；多次字符串/Unicode 拷贝可达到数十 GiB 进程内存，配置中的总限制没有统一执行点。
- SSE 在预算前物化历史；legacy history、recovery 和 broker drain 缺统一字节/时间/权限预算。
- SSE 预算只计算 content 且在 yield 后判断，转义后的实际帧可远大于原 content。
- ProcessExecutor 的 StreamReader 默认约 64 KiB，而业务合同允许 1 MiB 单行，超长行可能停止 drain。
- 极值 timestamp 不进 DLQ；新消息优先造成 PEL 饥饿；ingest stream 缺全局高水位。
- `result_data` 只有单帧限制，同一 run 可通过不同 key 无界扩大 Redis、JSONB 和 WAL。
- Artifact quota 是 4096-run 的进程内 LRU，重启、多副本和驱逐均可重置；无用户/项目/全局 quota。
- 项目导出预算只覆盖日志，不覆盖最多 200 条 execution 的 `result_data/error/stdout/stderr`。
- 前端日志只限制行数，不限制字节；长离线、history/end 之间断线仍有恢复缺口，持续突发流中存在 O(N x window) 数组移动。

### 5.4 前端功能与 API 合同

- 普通用户 Dashboard 请求 admin-only `/dashboard/metrics`，`Promise.all` 中一次 403 使整页状态不提交，并周期性重复失败。
- SpiderStats/Monitor 仍合成速率、健康、趋势、P99、retry 和 Worker 日志等运维数据，不能作为真实监控。
- 仓库导入前端基址是 `/api/v1/projects/import-from-repository`，后端实际为 `/api/v1/repositories/import-from-repository`，固定 404。
- 项目上传导入仍调用退役 API，固定 410；Agent 项目仍是无完整后端合同的入口；Rule region 未进入 FormData。
- JWT payload 使用普通 `atob`，不能可靠处理 base64url/Unicode，异常路径会触发错误的会话恢复行为。
- `ExecutionLogs` 未验证 URL 中 task/run 的归属；`loadExecution` 吞错误，外层仍提示刷新成功。
- Artifact/SpiderItems 失败显示为空；SpiderItems 固定只取 200 条且无分页，用户会把失败或截断误认为无数据。
- 多标签 refresh rotation、修改密码后的会话状态、SSE session revoke 仍存在跨标签错误注销或半认证状态风险。

## 6. P1 测试可信度问题

### P1-TEST-01 文件级 skip 隐藏真实失败

`tests/unit/test_worker_project_sync.py:7` 对整文件设置：

```python
pytestmark = pytest.mark.skip(reason="round5 TODO: mock Project.filter or init tortoise DB")
```

Core 分片显示 `683 passed, 9 skipped`。本轮通过进程内 pytest 插件临时移除该 marker（未修改源码），实际得到 `8 failed, 1 passed`。主要原因是测试继续 patch 已删除的 `antcode_core.infrastructure.storage.presign`，而生产 `WorkerProjectSyncService` 已不再实现旧 S3 预签名合同。

这不是可选依赖或外部环境导致的合理 skip，而是废弃合同、死代码和测试边界未完成。以整文件 skip 获得绿色分片违反项目 Debug-First 规则。

## 7. P2 可维护性与供应链

- 复杂度门禁绿色只表示 972 条历史违规没有新增或恶化，不表示满足硬限制。
- 当前 baseline 含 C901 35 条、超 300 行文件 185 个、PLR0911 15 条、PLR0912 20 条、PLR0915 18 条、magic number 407 条、位置参数超限 292 条。
- 最大文件仍包括 `workers.py` 2293 行、Worker `engine.py` 1782 行、Gateway transport 1685 行、Master scheduler loop 1518 行、Redis transport 1487 行。
- 基线以语句数近似函数长度，没有执行 50 个物理行的硬规则；前端也没有等价的 complexity/max-depth/max-params/no-magic-numbers 门禁。
- Docker workflow 在创建正式 tag 后才执行 Cosign；签名失败会留下未签名 tag。matrix `fail-fast:false` 也不能保证五个服务作为原子版本集合发布。
- Trivy 三处均使用 `ignore-unfixed:true`，因此“扫描通过”不等于不存在 High/Critical，只表示忽略了当前无上游修复的漏洞。

## 8. 自动化验证汇总

| 检查 | 结果 |
|---|---|
| Core 单元 | 683 passed / 9 skipped；移除不合理 skip 后 8 failed / 1 passed |
| Web API 单元 | 369 passed |
| Gateway + Master 单元 | 344 passed |
| Worker + Scripts 单元 | 691 passed / 6 skipped |
| Boundary | 15 passed |
| Contracts | 86 passed / 43 errors；错误均因本机 `localhost:16379` Redis 未启动 |
| Integration / E2E collect-only | 224 / 12 collected；不是执行通过 |
| Loadtest 自检 | 21 passed / 9 deselected；只是压测工具自检 |
| Ruff / mypy | 8 errors / 100 errors |
| 前端 lint / type-check / build | 全部失败 |
| 前端 Vitest | 19 files / 96 tests passed |
| Ruff format / diff check | 通过 |
| Complexity | 通过，972 条 baseline findings |
| Bandit High/High / pip-audit / npm audit | 0 / 0 known / 0 |
| Compose parse | 三个显式组合通过；默认 Makefile 路径失败 |
| 干净 HEAD import | Web API 通过；Master/Gateway/Worker 失败 |

Contracts 的 43 个 error 属本地依赖未启动，不能判为代码失败，也不能判为通过。真实 PostgreSQL migration/concurrency、Redis ACL/Cluster、fresh Docker、TLS/mTLS、备份恢复、Worker 凭据不可见性、浏览器 E2E、故障注入和压力测试均未完成。

## 9. 验收判定

1. 当前版本：**拒绝生产发布**。
2. 当前版本：**拒绝压测**。提交闭包、构建门禁、非 K8s 生产部署和身份隔离已有确定阻断，压测结果不具备上线判定价值。
3. K8s：**正式移出本项目验收范围**；其历史问题不再阻断，但也不计为修复。
4. 现有 dev/remote Compose：**仅限开发和隔离验收，不得作为生产画像**。
5. “已全部修复、稳定无错误”：**当前证据不支持该结论**。
