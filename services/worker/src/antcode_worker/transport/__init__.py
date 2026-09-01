"""
传输层模块

提供 Worker 与 Gateway/Redis 之间的通信抽象。
支持两种模式：
- Direct 模式：内网直连 Redis Streams
- Gateway 模式：公网通过 Gateway gRPC/TLS 连接

Worker 通过 transport.mode 明确选择 Direct（内网直连 Redis）或 Gateway（公网仅连 gRPC 网关）。
两种模式对 Engine 透明，统一遵循 poll→execute→report→ack 语义；
Direct 用 Redis Streams 消费组与 XAUTOCLAIM 保证 at-least-once，
Gateway 由网关代理 Redis/MySQL 并提供 TLS/认证/限流，确保中间件不暴露公网。

本包不做子模块聚合导入。聚合会让 ``transport.base`` 这类纯数据模块也拖进
gateway 的 grpc/protobuf 与 redis 客户端整条栈（实测 671 个模块），
子模块请直接从各自叶子模块导入。

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7
"""
