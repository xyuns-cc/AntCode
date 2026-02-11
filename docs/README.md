# 📚 AntCode 文档中心

欢迎来到 AntCode 文档中心！这里汇集了项目的所有技术细节、架构设计与操作指南。

为了帮助你更快找到所需信息，我们将文档按角色和场景进行了分类。

---

## 🧭 快速导航

### 我是... 新手开发者 (Onboarding)
如果你刚接触 AntCode，建议按以下顺序阅读：
1.  **[项目总览](../README.md)**: 了解 AntCode 是什么，以及如何快速启动。
2.  **[架构设计](ARCHITECTURE.md)**: 理解系统的核心分层（控制面 vs 执行面）与数据流向。
3.  **[本地开发指南](../README.md#快速开始本地开发)**: 手把手教你搭建开发环境。

### 我是... 核心贡献者 (Core Contributor)
深入理解系统内部机制：
-   **[数据库设计](database-setup.md)**: 数据库选型、Schema 定义与 Alembic 迁移指南。
-   **[Worker 通信原理](worker-transport.md)**: 深入剖析 Director 与 Gateway 两种接入模式的底层差异。
-   **[系统配置详解](system-config.md)**: 掌握所有环境变量与配置项的生效策略。
-   **[节点环境管理](node-env-management.md)**: 了解 Worker 如何自动管理 Python 运行时环境。
-   **[容错与恢复](resilience.md)**: 学习系统如何处理节点故障与网络分区。

### 我是... 前端与 API 开发者 (Frontend/API Developer)
对接后端接口与数据模型：
-   **[Web API 接口概览](../services/web_api/README.md)**: 快速了解 API 结构（或直接查看 [Swagger UI](http://localhost:8000/docs)）。
-   **[项目管理 API](project-api.md)**: Project 相关的核心接口逻辑。
-   **[任务调度 API](scheduler-api.md)**: 任务创建、触发与状态查询。
-   **[日志系统 API](logs-api.md)**: 实时日志流与历史日志查询接口。
-   **[用户与权限 API](user-api.md)**: 认证与授权模块。

### 我是... 运维工程师 (DevOps/SRE)
部署、监控与维护：
-   **[Docker 部署手册](../infra/docker/README.md)**: 容器化部署的最佳实践。
-   **[生产环境配置](system-config.md)**: 生产环境下的关键配置建议。
-   **[数据库维护](database-setup.md)**: 数据备份与恢复策略。

---

## 📂 数据与目录规范

为了保证系统的一致性，我们严格约定了运行时的数据存储路径。

**根目录**: `data/` (所有运行时数据均在此，且**不应**提交到 Git)

-   **`data/backend/`**: 控制面服务专用
    -   `db/`: SQLite 数据库文件（仅限开发环境）
    -   `logs/`: API、Master、Gateway 的服务日志
    -   `storage/`: 本地对象存储模拟
-   **`data/worker/`**: 执行面节点专用
    -   `projects/`: 下载的项目代码缓存
    -   `runtimes/`: 自动安装的 Python 虚拟环境
    -   `runs/`: 任务执行产生的临时文件
    -   `logs/`: 任务执行日志

---

## 📝 变更记录

| 版本 | 日期 | 说明 |
| :--- | :--- | :--- |
| **v3.1** | 2026-02-10 | 统一 `data/backend` 与 `data/worker` 目录结构，精简文档索引。 |
| **v3.0** | 2026-01-15 | 新架构发布，文档重构。 |

---

*文档发现错误？欢迎提交 PR 修正！*
