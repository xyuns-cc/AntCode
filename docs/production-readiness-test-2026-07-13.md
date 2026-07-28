# AntCode 生产就绪测试报告（2026-07-13）

## 1. 最终结论

当前不能认定“所有功能均已测试完成”，不能判定系统稳定无错误，不能进入正式压测，也不能发布到生产环境。

基础自动化、前端构建、真实 PostgreSQL/Redis 集成和 Direct Worker 沙箱均已通过大规模验证，但系统级验收仍有明确失败：Direct E2E 为 8/12 通过，Gateway E2E 为 2/12 通过，普通用户默认仪表盘可稳定复现 403/500。压测前置门禁未满足，因此本轮未执行压测。

## 2. 已通过测试矩阵

| 测试层 | 结果 |
| --- | --- |
| 后端 Unit + Boundary | 1216 passed，6 skipped；6 条仅限真实 Windows HANDLE/NTFS 语义 |
| Contracts | 122 passed |
| Integration | 144 passed |
| 前端 ESLint | 通过 |
| 前端 TypeScript | 通过 |
| 前端 Vitest | 39 passed |
| 前端生产构建 | 通过，3302 modules transformed |
| Worker 沙箱 | 真实 bubblewrap namespace 与 namespace 内 `prlimit` 探针通过 |
| Direct Worker 启动 | healthy，Redis DB12，Gateway endpoint 为空 |
| 管理员浏览器检查 | 登录、权限加载、Dashboard 数据加载通过 |

这些结果证明主要模块和大量局部契约可运行，但不能替代失败的跨服务真实链路。

## 3. Direct 模式 E2E

结果：`8 passed, 4 failed`，耗时 166.51 秒。

### 3.1 WebSocket 实时日志失败

- E2E 客户端没有回应服务端应用层 JSON `ping`，最终被 4008 关闭；生产前端会正确回应 `pong`，这一部分属于测试客户端缺陷。
- 任务日志在连接关闭前仍未到达。`RedisLogStreamService` 以 `$` 作为初始及空读后的游标，存在第一帧在两次 XREAD 之间到达时被跳过的风险。
- 服务端收到第一次 pong 后不重置 `last_pong`，后续永久丢失 pong 也不会再触发超时，是独立的真实状态机缺陷。

### 3.2 Rule Spider 提前成功

- Master 将 Rule 分发结果当作同步完成，在 Worker 返回最终结果前将 TaskRun 标记为 success。
- E2E 随后清理 TaskRun，Worker 的真实结果到达时被报“执行记录不存在”。
- Redis 中已存在 `Example Domain` item，证明爬虫执行和 spool relay 正常，错误位于 Master 终态处理。
- 分页分支还会派发未创建 TaskRun 的子 run，不能只补一个 pending 标记。

### 3.3 Retry 元数据丢失

- retry run 创建时正确写入 `retry_source_run_id`。
- 后续分发、pending 和结果持久化三处整体覆盖 `result_data`，最终丢失来源 run 标识。

### 3.4 Worker 心跳断言失败

- Worker 真实心跳周期为 30 秒。
- 测试只观察约 12 秒，因此该项是测试窗口错误，不是已证明的产品心跳故障。
- 环境恢复后已按真实周期轮询数据库，`last_heartbeat` 从 `16:14:04` 更新到 `16:14:34`，确认 Direct Worker 心跳链正常。

## 4. Gateway 模式 E2E

结果：`2 passed, 10 failed`，耗时 286.38 秒。

- 9 条任务、日志、取消、重试、失败、超时和 Spider 场景在 `ensure_shared_env` 阶段失败。
- 1 条 Worker heartbeat 因 12 秒测试窗口小于 30 秒真实周期而失败。
- 仅 2 条不依赖 Worker runtime 的用户 RBAC/会话安全测试通过。

### 4.1 Gateway runtime 控制协议未闭环

真实失败链如下：

```text
Web API 生成 UUID request_id 和 reply stream
-> Gateway 另行生成 control_stream|redis_msg_id receipt
-> Worker 使用 UUID 调 send_control_result
-> Gateway 将 UUID 按 AckControl.event_id 校验
-> event_id 不含 |，返回 received=false
-> Worker 抛“控制结果发送失败”
-> Web API 等待 reply stream 直至超时
```

测试机日志明确出现：

```text
AckControl 未知格式 event_id: <UUID>
控制结果发送失败: request_id=<同一 UUID>
```

更深一层，即使 Worker 改用正确 receipt，当前 Gateway Transport 也会忽略 `reply_stream` 和返回数据；Gateway 的 AckControl 仅执行 XACK，不会把 `list_envs/create_env` 的结果写回 Web API 等待的 reply stream。因此这不是单字段修复，而是 runtime 请求、业务结果和 Redis receipt 三种标识及响应通道尚未形成完整协议。

现有 fake Gateway 契约对任意 event_id 返回成功，且只检查内存 ACK，没有经过真实格式校验和 reply stream，因而未能发现该缺陷。

## 5. 浏览器功能实证

管理员账号可登录并加载 Dashboard。普通用户登录后默认进入 Dashboard，页面稳定显示“仪表盘数据加载失败”。网络结果为：

| 接口 | 普通用户结果 |
| --- | --- |
| `/dashboard/metrics` | 403 |
| `/workers/stats` | 403 |
| `/workers/stats/spider` | 403 |
| `/dashboard/tasks/hourly-trend` | 500 |

前三个接口是 Dashboard 对普通用户无条件请求管理员资源，任一 403 会使整个 `Promise.all` 失败。第四个接口按普通用户过滤 `TaskRun.created_by`，但 TaskRun 模型不存在该字段，真实异常为 `tortoise.exceptions.FieldError`。

## 6. 其他已确认缺陷

1. `scripts/run_worker.sh` 传入 `--gateway-host/--gateway-port`，CLI 只接受 `--gateway-endpoint`，实测脚本退出码为 2。
2. retry cancel API 可把 SUCCESS run 改为 CANCELLED，且不删除 Redis pending retry，终态可能被破坏且任务仍会执行。
3. Gateway 安装 Key 注册遇到重复 Worker 名时直接抛数据库异常并返回 500。
4. Direct/Gateway 切换存在持久凭据、旧 Redis URL、空环境变量和 `WORKER_ID` 优先级冲突。
5. Gateway Worker 在 gRPC 持续认证失败时健康探针仍可显示 healthy。
6. WebSocket 服务端首次 pong 后的超时状态不会进入下一轮检测。
7. `generate_proto.py`、真实 Gateway ResultHandler、飞书/钉钉/企业微信/邮件告警发送链仍缺少端到端或真实依赖回归。
8. Dashboard 24 小时趋势把全部 TaskRun 拉回 Python 聚合，尚未通过大数据量性能验证。

## 7. 覆盖完整性判断

不能声称“没有任何遗漏”。当前至少仍缺少以下已识别验证面：

- Gateway runtime 控制结果和真实 reply stream 契约。
- Gateway 任务结果 protobuf 写入、Redis 失败和 UNAVAILABLE 恢复。
- 多 Master 切主、网络分区、Redis/PostgreSQL 短暂中断后的系统级恢复。
- 四种外部告警通道的真实发送、TLS、重试和响应解析。
- 数据库旧版本快照升级、重复迁移、失败回滚与备份恢复演练。
- 大规模任务、日志、Spider 数据和 Dashboard 聚合下的容量与资源边界。

## 8. 测试机收尾状态

- 测试机：`192.168.1.250:2202`，项目 `/home/xinnn/antcode-20260713`。
- Web API、Master、Gateway、Worker、Frontend 均为 healthy。
- 临时 Gateway Worker 和测试 Gateway Worker 数据库记录已删除。
- Worker 容器及持久凭据卷已删除后重建，不保留 Gateway 凭据。
- 当前 Worker 为 `worker-e2e-01`，传输模式为 Direct，Gateway endpoint/host/port 均为空。
- PostgreSQL 中项目、任务、TaskRun、E2E 用户、安装 Key 均为 0；仅保留管理员与 Direct Worker。
- Redis DB14 已清空，`DBSIZE=0`。
- Redis DB12 清空后随服务重启生成 15 个正常运行态键，包括 leader、lease、heartbeat、队列和缓存，不存在本轮 E2E 项目或 Gateway Worker 残留。
- 临时 `antcode-e2e-git` 容器及 `/home/xinnn/antcode-20260713/e2e-git` 测试仓库目录已删除。
- 重建后五个服务最近 5 分钟日志未出现 ERROR/Traceback/Exception；Gateway 仅有测试环境预期的 insecure gRPC 警告。

## 9. 压测决定

本轮未执行压测。

压测必须建立在功能正确、关键 E2E 全绿、测试工具自身有效的前提上。当前 Direct 模式仍有 4 条失败，Gateway 模式有 10 条失败，普通用户默认页面存在稳定 403/500。此时执行压测只能得到错误路径、超时堆积和不完整功能的吞吐数字，不能证明系统容量、稳定性或生产可用性。
