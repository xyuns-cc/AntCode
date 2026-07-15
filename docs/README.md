# AntCode 文档

按主题分类。开始之前建议先看顶层 [`README.md`](../README.md) 了解总貌。

## 入门

- [顶层 README](../README.md) — 项目介绍 + 5 步跑通
- [`database-setup.md`](database-setup.md) — 数据库初始化 (`init_db.py` 全流程)

## 架构

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — 系统架构总览、数据流、部署拓扑
- [`resilience.md`](resilience.md) — 熔断 / 重试 / 补派 / 幂等设计
- [`scrapy-migration.md`](scrapy-migration.md) — 规则爬虫引擎从自建 spiderkit 迁到 Scrapy 的经过和契约

## 部署

- [`worker-transport.md`](worker-transport.md) — Direct（Redis Streams）vs Gateway（gRPC）传输模式的取舍
- [`worker-capabilities.md`](worker-capabilities.md) — Worker 能力路由（code-only / rule-only worker 部署）
- [`redis-cluster.md`](redis-cluster.md) — Redis 单机 / 集群 / 哨兵配置
- [`master-scaling.md`](master-scaling.md) — Master 多实例水平扩容（leader-elect + loop 分片）
- [`mtls-deployment.md`](mtls-deployment.md) — Gateway mTLS 部署（跨公网场景）
- [`node-env-management.md`](node-env-management.md) — 多语言运行时（mise）管理

## 系统 API

- [`user-api.md`](user-api.md) — 用户 / 认证 / 权限
- [`project-api.md`](project-api.md) — 项目 CRUD + 规则爬虫配置
- [`scheduler-api.md`](scheduler-api.md) — 调度器（cron / 一次性 / 周期性 / 依赖链）
- [`logs-api.md`](logs-api.md) — 任务日志历史查询 + SSE 实时推送
- [`system-config.md`](system-config.md) — 系统级配置项（告警、调度、保留策略）

## 运维

看板与告警：
- Prometheus `/metrics` — web_api 挂在 `/metrics`，worker 挂在 `:8001/metrics`
- 关键指标：`antcode_http_requests_total{path,status}` / `antcode_http_request_duration_seconds` / `antcode_workers_online` / `antcode_redispatch_pending`

日志：
- 结构化日志走 loguru，容器化部署建议 stdout 让日志收集器兜（`LOG_TO_FILE=false`）
- 业务任务日志走 Redis Stream → PG `task_logs`，前端 WS 实时订阅

## 版本变更

- [`../CHANGELOG.md`](../CHANGELOG.md) — 每个 release 的变更

## 内部说明

- [`../migrations/models/README.md`](../migrations/models/README.md) — 首版无迁移策略 + 后续演进方案
- 内部审计报告、开发过程记录在正式发版时已从 repo 移除。历史请查 git log。
