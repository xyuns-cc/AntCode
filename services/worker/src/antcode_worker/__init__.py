"""
AntCode Worker 执行器服务

Execution Plane 的核心组件，负责：
- 任务执行
- 运行时管理（uv 环境）
- 日志输出（实时流 + 归档）
- 心跳上报

支持两种传输模式：
- Direct 模式：内网直连 Redis Streams
- Gateway 模式：公网通过 Gateway gRPC/TLS 连接

本包不做子模块聚合导入。Rule 沙箱内以 ``python -m
antcode_worker.executor.rule_network_relay`` 启动的 relay 会先执行本文件，
聚合导入会把整个 engine/transport 栈拉进来并实例化控制面 ``Settings()``；
沙箱按 C1 allowlist 刻意不继承 DATABASE_URL，relay 因此直接导入失败。
"""
