# WebSocket -> SSE 实时日志专项审查报告（2026-07-16）

## 1. 结论

当前版本**不能进入压测，不能发布生产**。

测试机的最终一致镜像中，历史日志查询与经 Nginx 的历史回放可用，但 Direct Worker 写入 Redis 的实时日志使用 protobuf `LogBatch`。Web API 镜像没有安装 `antcode_contracts`，SSE follower 无法导入协议类型，随后错误地把 protobuf 二进制退回旧 JSON 解码路径并触发 UTF-8 解码异常。最终表现为：SSE HTTP 连接保持 200、15 秒心跳仍可发送、前端显示“已连接”，但实时日志永远不到达，readiness 仍为 healthy。

除该发布阻断问题外，本轮还确认 Redis follower 首帧竞态、双 Web API worker 的跨进程推送缺失、sequence 非原子、会话重校验 fail-open、前端暂停丢日志、EventSource 重连竞态、连接内存无字节边界等问题。

## 2. 审查范围与环境

- 本地工作区：`/Users/xinnn/Project/PythonProject/AntCode`
- 基准提交：`afdc574cbf3923d511bb424e36979acb7f63a18f`
- 审查对象：上述提交加当前未提交工作区修改；工作区不是 clean 状态，不能只用 commit SHA 表示部署内容。
- 测试机：`192.168.1.250:2202`
- 测试目录：`/home/xinnn/antcode-20260713`
- PostgreSQL：专用 `antcode_e2e_test`，迁移测试使用 `antcode_migration_test`
- Redis：运行环境 DB 12，Integration/Contracts 使用 DB 14
- 部署：Web API、Master、Gateway、Worker、Frontend 五个最新镜像；最终均 healthy
- Web API 实际进程：2 个 Uvicorn worker
- 浏览器路径：本地 SSH 转发 -> 测试机 Frontend Nginx -> Web API

测试期间发现第一次远端源码同步包含新调用方和旧被调方。随后用 SHA256 定位并单文件校验同步；最终运行镜像的 `PostgresLogService.list_entries` 已确认包含 `latest` 参数。本文的 SSE 最终失败结论来自修正同步后的镜像，不是该部署不一致造成的假失败。

## 3. 发布阻断问题

### P0-01 Web API 镜像缺失 protobuf 合同包，实时 SSE 实际不可用

证据：

- `services/web_api/src/antcode_web_api/streams/ingest_follower.py:235` 和 `:264` 运行时导入 `antcode_contracts.data_pb2`。
- `services/web_api/pyproject.toml:6-24` 只声明 `antcode-core`，没有声明 `antcode-contracts`。
- `infra/docker/Dockerfile.web_api:58-60` 使用 `uv sync --package antcode-web-api`，因此不会安装未声明依赖。
- Dockerfile 虽在 `:87` 把源码复制到 `/app/packages/antcode_contracts/src/antcode_contracts`，但运行时 `PYTHONPATH` 只有 `/app`；实际 `sys.path` 包含 core/web_api editable 路径，不包含 contracts 的 `src`。
- 测试机容器实测 `import antcode_contracts` 直接抛 `ModuleNotFoundError`。
- follower 捕获导入/解析异常后在 `ingest_follower.py:278-280` 静默转入旧 JSON 路径；`control_plane.py:203-209` 对 protobuf bytes 执行 UTF-8 解码，再次抛异常。
- 测试机日志稳定出现：`ingest stream 读取失败: 'utf-8' codec can't decode byte ...`。

影响：

- Direct 和 Gateway 写入全局 ingest stream 的 protobuf 实时日志均无法通过 SSE 推送。
- HTTP 连接、ping 和 health check 都保持正常，形成“连接健康但没有实时数据”的假健康状态。
- 前端历史回放成功后持续显示“已连接”，用户无法知道 follower 已失效。

测试缺口：本地全 workspace 环境天然安装了 `antcode_contracts`，现有单测无法发现生产镜像缺包；CI 缺少 Web API 镜像内 `import antcode_contracts` 和真实 protobuf Redis -> SSE smoke test。

## 4. 后端正确性与安全问题

### P1-01 Redis follower 会丢首批实时日志

`ingest_follower.py:53-77` 的 `follow()` 只创建后台 task，不等待首次 XREAD 建立；`:176` 使用 `$`，`:185-186` 空读后继续沿用 `$`。Redis 会在每次 XREAD 调用时重新解释 `$` 为当时最新 ID，因此首次阻塞建立前，以及 block timeout 与下一次 XREAD 之间到达的消息会被跳过。现有 E2E 延迟 10 秒输出，未覆盖握手窗口。

### P1-02 双 Web API worker 下 HTTP 实时推送跨进程失效

`packages/antcode_core/src/antcode_core/common/config.py:103` 默认 `SERVER_WORKERS=2`。HTTP `report-log` 只调用命中进程的 `distributed_log_service`，`streams/log_notifier.py:18-30` 只查询/发布该进程内 broker。SSE 在进程 A、上报落进程 B 时，实时帧不会送达，只能刷新后从 PostgreSQL 恢复。

### P1-03 sequence 分配跨进程重复、倒退且提交顺序不稳定

`distributed_log_service.py:31-32` 只有进程内 dict/lock。两个 Web API worker 可同时从同一 PostgreSQL 高水位生成相同 sequence；数据库索引不是唯一分配器。`:159-166` 在高水位查询失败时从 0 静默编号，恢复后缓存也不会重新播种。SSE 在 `log_stream_service.py:279-287` 以 sequence 过滤历史重叠，真正的新日志可能被误判为旧日志并丢弃。

### P1-04 存活 SSE 的会话重校验 fail-open

`log_stream_service.py:112-131` 捕获所有数据库异常并返回 `True`。管理员撤销会话或禁用用户时，如果 PostgreSQL 在重校验窗口不可用，该连接仍可从 Redis 接收受保护日志；持续故障时最长可保留 8 小时。单测还明确固化了 DB down -> `True`，与项目“禁止静默 fallback”规则冲突。

### P1-05 follower 初始化失败后不恢复，并可能泄漏订阅

`ingest_follower.py:167` 的 `get_redis_client()` 位于循环重试 try 外。初始化失败会终止 task，现有连接只剩 ping；Redis 恢复后不会自动重启。`unfollow()` 等待失败 task 时会重新抛出，`log_stream_service.py:183-187` 又在 broker unsubscribe 前等待 unfollow，导致订阅计数和队列残留。

### P1-06 readiness 不覆盖实时日志 follower

本轮实际发生 follower 持续异常时，五个容器仍全部 healthy，浏览器也显示“已连接”。当前 readiness 只检查 PostgreSQL/Redis 基础连接，没有检查 follower task 状态、last-id 或消费延迟。

## 5. 前端正确性与安全问题

### P1-07 暂停期间日志永久丢失

`EnhancedLogViewer.tsx:155-158` 在暂停时直接丢弃消息；只有暂停期间恰好发生历史重放时，`:281-283` 才设置 `pendingResync`。稳定连接下暂停 -> 产生日志 -> 恢复，不会触发补同步，缺行永久存在。

### P1-08 EventSource 重连存在旧回调和孤儿连接竞态

`logs.ts:228-247` 的 teardown 操作共享可变 `source`；`:259-269`、`:372-393` 没有 generation、in-flight 或可取消 reconnect timer。重复/延迟 `onerror` 可同时创建多个 EventSource，旧连接回调还能关闭新连接，未被全局引用的连接最长存活 8 小时并重复回放/推送。

### P2-01 `autoConnect=false` 时手工重连和暂停恢复可能失效

`EnhancedLogViewer.tsx:716-719`、`:872-876` 的 `setTimeout(connect)` 捕获旧闭包，旧状态仍为 connected 时会在 `connect` 入口直接 return。timer 也没有在卸载时取消。

### P2-02 默认自动连接使“断开”按钮无法保持断开

浏览器实测点击“断开”后约 800ms 又显示“已连接”。原因是 `EnhancedLogViewer.tsx:490-504` 观察到 disconnected 后自动调用 `connect()`。界面提供显式“断开”控制，但默认模式下用户不能真正暂停网络连接。

### P2-03 历史截断信息被前端丢弃

后端 `historical_logs_end` 可携带 `truncated=true`，但 `logs.ts:332-341` 忽略该字段，页面仍显示“历史日志加载完成”。超过 10000 行时用户无法知道历史不完整；组件 `maxLines=5000` 还会再次裁剪。

### P2-04 高吞吐下主线程计算量过大

`EnhancedLogViewer.tsx:155-168` 每帧复制最多 5000 条数组，`:375-400` 重复过滤，`:449-459` 多次 filter 并触发额外状态更新。10k 历史回放或持续高吞吐可能阻塞主线程并诱发服务端慢消费者 overflow。虚拟化只减少 DOM，不减少这些 O(events * buffer) 计算。

### P2-05 CSV 公式注入

`EnhancedLogViewer.tsx:425-430` 只转义双引号，没有中和以 `=`, `+`, `-`, `@` 开头的不可信日志单元。导出 CSV 后用 Excel 打开可能执行公式。日志页面本身使用 React 文本节点，未发现 DOM XSS。

## 6. 容量、停机与可观测性问题

### P1-09 历史回放与实时队列可形成重连活锁

每连接实时队列固定 5000（`run_stream_broker.py:27-30`），历史最多回放 10000 条；服务先订阅再完整回放，直到回放结束才消费实时队列。若回放期间新增日志达到 5000，队列溢出并断流；客户端重连后再次全量回放，持续写入时可无限重复。

### P1-10 连接和历史均无字节上限，存在 OOM 风险

默认每进程允许 20000 连接，每连接 5000 个队列槽；HTTP 单行日志没有 `max_length`，只受 10 MiB 请求体总限制。连接分散到不同 run 时，理论积压可达到数十至上百 GB。历史读取还会为每连接完整物化最多 10000 个 dict。远端 Compose 没有 Web API memory/ulimit/resource limit。

### P1-11 连接限制和统计均为进程内数据

默认 2 workers 时 total/per-run/per-user 上限实际可达到配置值 2 倍，多副本继续倍增；`/stream/stats` 只返回命中进程的局部值，不能用于部署级容量判断。

### P1-12 活跃 SSE 可能阻碍优雅停机

连接最长 8 小时，Uvicorn 未配置 `timeout_graceful_shutdown`，lifespan 也没有显式关闭 broker/follower，Compose 未设置 `stop_grace_period`。有活跃 SSE 时滚动发布可能一直等流结束，最终依赖容器硬杀。

### P2-06 legacy Redis 历史回落绕过 10000 限制

`ingest_follower.py:131-158` 的旧 per-run stream 回落没有 limit，会一直 XREAD 到流尾，绕过 `HISTORY_LIMIT` 与 `truncated` 契约。

### P2-07 缺少关键 SSE 指标

当前没有队列深度/字节、publish/drop rate、历史耗时、连接时长、ticket/limit 拒绝、follower last-id/lag/task 状态。Prometheus HTTP 指标要等长连接关闭才记录，且同样是 worker-local。

## 7. 非 SSE 功能回归

### P1-13 Rule Spider 任务成功但抓取数据为空

`tests/e2e/test_spider_data_flow.py` 在测试机失败：TaskRun 为 success，但查询结果 `items=[]`，未找到预期 `Example Domain` 数据。该问题在当前相同源码快照上可重复。

### P1-14 Retry 来源元数据仍会丢失

`tests/e2e/test_task_cancel_retry.py::test_failed_task_retries_once` 失败：两个 retry run 均为 failed 且 run_id 不同，但 `result_data` 中都没有 `retry_source_run_id`。`scheduler_loop.py:978-990` 创建时写入该字段，后续仍存在整体覆盖 `result_data` 的路径。

### P2-08 默认 Python 3.12 运行时安装在测试机不可用

E2E 默认 `shared-py312` 创建连续失败，Worker 的 `uv 0.5.31` 三次重试均报 GitHub DNS lookup failure；同一容器的 `getent` 和 `curl https://github.com` 可成功。为了继续验证任务主链，后续 E2E 使用镜像内置的真实 Python 3.11。该现象需要作为当前部署运行时创建能力失败处理，不能把 3.11 通过等同于 3.12 已通过。

### P2-09 Worker 心跳 E2E 的观察窗口错误

自动测试只观察约 12 秒，而 Worker 实际上报间隔为 30 秒，因此 `test_worker_heartbeat` 失败不证明产品心跳故障。直接查询 PostgreSQL 观察 35 秒，`last_heartbeat` 从 `07:41:37` 更新到 `07:42:07`，真实心跳链通过。

## 8. 结构与可维护性

以下文件超过项目 AGENTS.md 的 300 行硬限制：

| 文件 | 行数 |
| --- | ---: |
| `services/web_api/.../streams/ingest_follower.py` | 320 |
| `services/web_api/.../streams/log_stream_service.py` | 305 |
| `packages/antcode_core/.../distributed_log_service.py` | 333 |
| `web/.../services/logs.ts` | 415 |
| `web/.../EnhancedLogViewer.tsx` | 964 |

`connectLogStream` 约 214 行且有 6 个位置参数，连接状态机、ticket、协议解析、watchdog、重连混在同一函数。现有 complexity baseline 通过只说明没有超过已接受债务，不代表满足当前硬限制。

此外仍有过期 WebSocket 命名/文案：loadtest 参数说明、日志性能统计字段、日志安全错误分支及 scheduler 注释。它们不是本轮主故障，但会继续误导维护与监控。

## 9. 测试结果

| 测试层 | 结果 |
| --- | --- |
| 测试机 Unit + Boundary | `1243 passed, 6 skipped`，24.40s |
| 最终 SSE/PG 定向单测 | `22 passed` |
| 测试机 Integration | `144 passed`，真实 PostgreSQL/Redis |
| 测试机 Contracts | `122 passed`，Direct + fake Gateway |
| 测试机前端 Vitest | `48 passed` |
| 前端 TypeScript / ESLint / production build | 镜像构建阶段通过 |
| 本地 Mypy | 422 files，0 issues |
| 本地 complexity gate | 通过，396 条 baseline finding |
| 浏览器登录/Dashboard/任务/历史日志 | 通过 |
| Nginx SSE 历史首屏 | 通过，历史日志可立即显示 |
| 实时 SSE E2E | **失败**，60 秒硬超时；follower protobuf 解码失败 |
| Log API raw/structured | 通过 |
| Rule Spider 数据 E2E | **失败**，TaskRun success 但 items 为空 |
| Retry E2E | **失败**，来源元数据丢失 |
| 用户 RBAC/会话 E2E | `2 passed` |
| 失败/超时任务 E2E | `2 passed` |
| 普通任务生命周期 E2E | `1 passed` |
| Trigger 去重 E2E | `1 passed` |
| Worker 心跳 | 自动用例窗口错误；35 秒数据库实测通过 |

现有 SSE 单测全部通过却没有发现生产镜像缺依赖、真实 protobuf、双 worker、首帧竞态、暂停恢复、重复 `onerror`、Nginx、慢消费者和滚动停机场景，说明测试层级存在明显断层。

## 10. 压测决定

本轮**未执行压测**。实时 SSE 主功能已经在真实部署中失败，Spider 数据与 retry 元数据也未全绿。此时压测只会测到错误路径、心跳空连接和重连堆积，无法证明系统容量或稳定性。

## 11. 最终判定

- 代码层：存在 P0 发布阻断及多项 P1 并发/安全/容量问题。
- 功能层：历史日志可用，实时日志不可用；另有 Spider 与 retry 回归。
- 部署层：五容器 healthy，但 health/readiness 不能代表 SSE follower 健康。
- 稳定性：不能声称稳定无错误。
- 生产结论：**不具备生产发布条件**。
