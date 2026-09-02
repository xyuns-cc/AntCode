# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式和 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.0] — 首次发布

首个稳定发行版。定位是分布式任务调度与执行平台，主要面向规则爬虫、脚本任务和文件处理场景。

### 核心能力

#### 调度与执行
- 一次性 / 周期性 / Cron 表达式调度
- 任务依赖链（DAG-lite）
- Worker 插件化：`code` / `spider` / `render` / `rule` 四类
- Direct（Redis Streams）+ Gateway（gRPC）双传输模式，同一执行引擎
- 沙箱化子进程：POSIX rlimit 强制约束 CPU / 内存 / 打开文件 / 单文件大小
- 优雅停机：SIGTERM 触发 drain（30s 默认）+ 主动 revoke lease + 强杀兜底

#### 规则爬虫（Scrapy 引擎）
- Scrapy 2.16 底座
- 5 种翻页策略：`url_pattern` / `url_param` / `click_element` / `js_click` / `infinite_scroll`
- 抽取：XPath / CSS / 正则三选一，按字段组合
- `{N}` 起始页占位符 + XPath / text / 结构化 next 选择器
- Playwright 渲染
- 安全 spool 模式不向 Rule 子进程下发 Redis / 代理凭据，因此内容级去重、代理池、
  scrapy-redis 断点续爬三项配置只要开启就**直接报错中止**，不静默降级；
  `engine=curl_cffi` 需自行安装 `scrapy-impersonate`（不在依赖里）
- gateway 模式下 spider data 走 `DataService.StreamSpiderData` gRPC，与 direct 模式字段字节级一致

#### 可靠性
- At-least-once 派发：XREADGROUP + PEL + XAUTOCLAIM 孤儿回收（min_idle 60s）
- 跨机 run_id 去重：`{ns}:run:owner:<run_id>` fencing，与 lease key 同 hash slot
- 派发失败自动补派：Redis ZSet 持久化队列 + 指数退避（30s → 300s，最多 5 次）+ 超阈值 audit + 告警
- Master leader 抢主 + 主备切换（TTL 30s，10s verify）
- Master 共 12 个后台 loop。`result` / `log_ingest` 走 consumer name 天然分区，可分片到多实例；
  `scheduler_outbox` 走 PG 行级 claim；其余 9 个 leader-only

#### 观测
- Prometheus `/metrics` 端点：http_requests / duration / workers_online / redispatch_pending
- 全链路 W3C traceparent 透传（gateway → worker → 子进程）
- SSE 实时日志：单 run_id 一条后台 XREAD，多客户端 fan-out（不占独立 Redis 连接）
- 结构化日志（loguru + trace_id），可选文件 rotate

#### 安全
- JWT + 项目 / 批次所有权校验
- 登录专项限流（IP 5/min + 用户名 10/15min）+ 账户锁定（连续失败 5 次锁 15 分钟）
- Redis 分布式滑动窗口全局限流（Lua 原子 + 受信代理白名单防 XFF 伪造）
- 密钥轮换：`MultiFernet(primary + legacy)`，`ENCRYPTION_KEYS_LEGACY` 支持解密老密文，加密始终用当前 key
- Git 凭证 / Redis 密码等敏感字段加密落库
- Worker 子进程 env 按子串黑名单剔除：`SECRET` / `PASSWORD` / `TOKEN` / `API_KEY` / `CREDENTIAL` / `PRIVATE_KEY`
- gRPC gateway mTLS 部署方案

#### 存储与扩展
- Redis 单机 / 集群 / 哨兵三种部署形态自动分派（URL scheme 或 `REDIS_MODE` env）
- Lease key hash-tag（`{ns}` 整体加 tag，即 `{ns}:lease:data:<worker_id>`）保证集群下 Lua 单 slot
- 保留策略：`task_logs` / `audit_logs` / `worker_events` 批式 DELETE（`LIMIT N` 循环），避免长事务锁表
- Worker capability 上报 + Master dispatcher 按 task_type 路由。`WORKER_ENABLE_RULE_PLUGIN=false`
  可部署 code-only worker；Code / Spider / Render 插件无条件注册，没有 rule-only 开关

### 数据库

- **无迁移链**：v1.0.0 由 ORM model 一次性建表，不携带任何存量库升级路径。
- 一键初始化：`uv run python scripts/init_db.py`

### 部署

- Docker Compose 模板：[`infra/docker/docker-compose.dev.yml`](infra/docker/docker-compose.dev.yml)
- 单机开发：`uv sync --all-packages` + `python scripts/init_db.py` + 三命令起服务
- 生产建议：HTTPS 收口 / PG 每日备份 / 强密钥 / Prometheus + Grafana

### 已知限制

- Scrapy 子进程冷启动 ~1.5-3s（每 run 一个 python 进程；未来可考虑常驻池）
- Master 单实例 leader-only loop（reconcile / cleanup 等）不分片；shardable loop 可水平扩容
- Gateway 模式下 rule 任务的 spider data 走 gRPC，比 direct 模式多一跳
- 前端未做国际化，仅中文界面

### 参与开发

- 源码结构见 README "目录" 节
- 提 issue / PR 前请先跑 `make release-gate`，或至少确保 `init_db.py` + 三服务能起来

[1.0.0]: https://github.com/xyuns-cc/AntCode/releases/tag/v1.0.0
