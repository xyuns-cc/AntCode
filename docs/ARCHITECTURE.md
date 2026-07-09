# 🏗️ AntCode 系统架构深度解析

本文档将带你深入了解 AntCode 的设计理念、分层架构以及关键的数据流转机制。

---

## 🎯 核心设计理念

在设计 AntCode 时，我们始终坚持以下三个核心原则：

### 1. 控制与执行分离 (Separation of Control and Execution)
这是系统稳定性的基石。
-   **控制面 (Control Plane)**：负责"思考"。它决定什么时候运行任务，任务该分发给谁。如果控制面挂了，仅仅是不能产生新任务，正在运行的任务不受影响。
-   **执行面 (Execution Plane)**：负责"行动"。它是无状态的，只管执行收到的指令。如果某个 Worker 挂了，控制面会感知并重新调度，不会拖累整个系统。

### 2. 双模接入 (Dual-Mode Transport)
为了适应不同的网络环境，我们设计了两种 Worker 接入模式：
-   **Direct 模式**：Worker 直连 Redis。适用于内网环境，通信链路最短，延迟极低。
-   **Gateway 模式**：Worker 通过 gRPC 网关接入。适用于跨公网、跨云环境，Worker 无需暴露在公网，安全性极高。

### 3. 数据隔离与规范 (Data Isolation)
系统状态以 PostgreSQL、Redis 和 Git 为唯一共享事实源。PostgreSQL 持久化业务元数据、历史日志、source bundle 与执行产物；Redis 只承载实时队列、流式日志和心跳；Git 是项目源码入口。Worker 本地目录只用于单次运行的临时解包、虚拟环境缓存和身份材料，不作为业务数据存储。

---

## 🧩 服务分层架构

AntCode 的架构可以清晰地划分为四个平面：

| 平面 | 服务组件 | 核心职责 | 关键技术 |
| :--- | :--- | :--- | :--- |
| **Control Plane** | `web_api` | **大脑**。提供 RESTful API，处理用户请求、鉴权、配置管理以及元数据存储。 | FastAPI, PostgreSQL |
| **Schedule Plane** | `master` | **心脏**。负责任务的调度分发、故障检测、重试机制以及数据的一致性维护。 | Python AsyncIO, Redis Streams |
| **Data Plane** | `gateway` | **关口**。为公网 Worker 提供统一的 gRPC 接入点，负责认证、限流与协议转换。 | gRPC, TLS |
| **Execution Plane** | `worker` | **手脚**。实际执行任务的节点，支持沙箱隔离、资源限制与实时日志上报。 | Subprocess, Streaming |

---

## 🔄 关键数据链路

### 1. 任务调度链路 (Task Flow)

一个任务从创建到完成，经历以下流转：

1.  **提交 (Submit)**: 用户通过 API 创建任务，`web_api` 将任务元数据写入 PostgreSQL。
2.  **打包 (Bundle)**: `master` 读取项目绑定的 Git 仓库来源，生成 source bundle，并把 bundle 与产物 blob 写入 PostgreSQL。
3.  **调度 (Schedule)**: `master` 将只含 `pgartifact://` source bundle 引用的任务消息投递到 Redis Stream 队列。
4.  **拉取 (Pull)**:
    -   **Direct Worker**: 直接从 Redis Stream `XREADGROUP` 拉取消息。
    -   **Gateway Worker**: 发起 gRPC `StreamTasks` 请求，Gateway 从 Redis 拉取后转发给 Worker。
5.  **执行 (Execute)**: Worker 通过 `pgartifact://` 读取 source bundle，准备 Python 环境，启动子进程执行。
6.  **上报 (Report)**: 执行结果与状态通过 Redis 或 Gateway 回流，由 `master` 更新 PostgreSQL。

### 2. 日志传输链路 (Log Flow)

AntCode 要求日志也是实时的：

1.  **采集**: Worker 捕获子进程的 `stdout/stderr`。
2.  **传输**: 
    -   Worker 将日志分片 (Chunk) 压缩。
    -   通过 Redis Stream (Direct) 或 gRPC Stream (Gateway) 发送。
3.  **持久化**: `master` 或专门的 Log Consumer 消费日志流，将历史日志写入 PostgreSQL `task_logs` 表。

---

## 📁 代码组织结构

我们采用 Monorepo 结构，但模块边界清晰：

```text
AntCode/
├── packages/                  # 🟢 共享基座
│   ├── antcode_core/          # 包括领域实体、工具类、配置对象
│   └── antcode_contracts/     # Proto 定义与生成的代码
├── services/                  # 🔵 微服务
│   ├── web_api/               # 控制面 API
│   ├── master/                # 调度器
│   ├── gateway/               # gRPC 网关
│   └── worker/                # 执行节点
├── contracts/proto/           # 原始 Proto 文件
├── infra/docker/              # 容器编排配置
└── data/                      # 🔴 本地临时目录、Worker 身份和运行时缓存 (Git Ignore)
```

---

## 🔌 Worker 接入模式对比

| 特性 | Direct 模式 | Gateway 模式 |
| :--- | :--- | :--- |
| **适用场景** | 内网、私有云、单机开发 | 公网、混合云、跨地域部署 |
| **依赖** | 需要直连 Redis | 仅需连接 Gateway 端口 (50051) |
| **通信协议** | Redis Protocol (RESP) | gRPC / HTTP2 |
| **安全性** | 依赖网络隔离 | 依赖 API Key 认证 + TLS 加密 |
| **性能** | 极高 (少一跳) | 高 (gRPC 带来的开销很小) |

> **最佳实践**: 在由于同一 VPC 内，优先使用 Direct 模式；只要跨越了网络边界，请务必使用 Gateway 模式。

---

## 🚀 下一步

- 了解如何配置数据库：[Database Setup](database-setup.md)
- 深入 Worker 配置：[Worker Configuration](system-config.md)
