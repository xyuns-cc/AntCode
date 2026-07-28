# Worker TransportBase 共享契约测试

本目录是 **Worker 传输层共享契约测试集**(代号 **P6**),目的是确保
`antcode_worker.transport.base.TransportBase` 的两个具体实现
(`RedisTransport` / Direct 模式与 `GatewayTransport` / Gateway 模式)
**永远共享同一套行为契约**。

后续 P1/P2/P3 任何对传输层的重构、协议改动、连接策略变化,
都必须先让本目录下的测试在两种 transport mode 下同时通过,
才能合入主干。

## 设计要点

- `transport_mode` fixture 让每个共享契约双跑 Redis Direct 与 Gateway 实现,
  使契约差异立即暴露。
- 测试只接触 `TransportBase` 的公开接口(详见 `services/worker/src/antcode_worker/transport/base.py`),
  不会去摸具体实现的私有属性。
- Gateway 使用 function-scope 的 in-process `grpc.aio` fake server,覆盖真实
  protobuf 序列化、server/client streaming、ACK 与 Lease RPC,不依赖 Redis
  或 PostgreSQL。
- 后端差异由 `ContractProbe` 适配为同一组语义断言,不会把 Gateway 失败
  降级为 skip。

## 运行方式

```bash
# 1. 起一个一次性的 Redis 容器(端口 16379,不与本机默认 6379 冲突)
docker compose -f tests/contracts/docker-compose.test.yml up -d

# 2. 在仓库根目录跑契约测试；默认使用 Redis DB 14
uv run pytest tests/contracts/ -v

# 只运行不依赖 Redis 的 Gateway 契约
uv run pytest tests/contracts/ -v -k gateway

# 3. 跑完清理
docker compose -f tests/contracts/docker-compose.test.yml down -v
```

如果 `localhost:16379` 上没有 Redis，Redis Direct 与 LeaseStore 契约会
明确失败；Gateway 契约仍会通过进程内 fake server 真实运行，但不能替代
Redis 契约覆盖。

`unacked_task_is_reclaimable_after_disconnect` 会把测试后端的消息 idle 时间
推进到各自生产阈值,再验证 at-least-once 重投：Direct 使用真实
`ReclaimConfig.min_idle_time_ms`,Gateway 使用真实
`TaskPollHandler.PENDING_VISIBILITY_TIMEOUT_MS`。测试不会缩短运行时 TTL。

## 文件组织

| 文件 | 覆盖契约 |
|---|---|
| `conftest.py` | 参数化 fixture + sys.path 注入 + cleanup |
| `test_transport_lifecycle.py` | `start` / `stop` / `state` / `is_running` |
| `test_transport_task_flow.py`  | `poll_task` / `ack_task` / `requeue_task` / `report_result` |
| `test_transport_logs.py`       | `send_log` / `send_log_batch` / `send_log_chunk` |
| `test_transport_control.py`    | `poll_control` / `ack_control` / `send_control_result` |
| `test_transport_heartbeat.py`  | `send_heartbeat` |
| `test_transport_resilience.py` | 重连、空轮询、退避 |

## 为什么不放在 services/worker/tests/?

`services/worker/tests/` 是 Worker 单体的内部测试,
依赖具体实现的私有细节、可以随实现一起改。
而本目录是 **跨实现** 的契约层,
连 services/worker 自己也不能擅自修改契约。
所以放在仓库根 `tests/contracts/` 下,
未来即使 worker 模块拆分,这套契约也跟着走。
