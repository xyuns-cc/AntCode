"""
AntCode Gateway Service - Data Plane

gRPC 网关服务，负责：
- 公网 Worker 节点的 gRPC/TLS 通信
- 认证与授权（mTLS/API Key/JWT）
- 请求限流与熔断
- 代理 Worker poll 任务（从 Redis Streams 读取）
- 接收日志整批写入全局 <namespace>:log:ingest stream（不再按 run_id 拆流）
- 接收结果并回写 PostgreSQL

职责边界：
- 不实现复杂调度策略（只代理队列 + 写状态/日志/结果）
- 不处理业务 CRUD

包根**不再**转出 AuthInterceptor / RateLimitInterceptor / GrpcServer：既没有调用方
（全部走子模块导入），又会制造导入环 —— ``auth`` 依赖 ``auth_credentials`` 等兄弟
模块，导入兄弟模块会先执行本文件，而此时 ``antcode_gateway.auth`` 还没执行完，
``from antcode_gateway.auth import AuthInterceptor`` 必然 ImportError。
请直接从子模块导入。
"""
