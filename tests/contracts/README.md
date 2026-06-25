# Worker TransportBase 共享契约测试

本目录是 **Worker 传输层共享契约测试集**(代号 **P6**),目的是确保
`antcode_worker.transport.base.TransportBase` 的两个具体实现
(`RedisTransport` / Direct 模式与 `GatewayTransport` / Gateway 模式)
**永远共享同一套行为契约**。

后续 P1/P2/P3 任何对传输层的重构、协议改动、连接策略变化,
都必须先让本目录下的测试在两种 transport mode 下同时通过,
才能合入主干。

## 设计要点

- 每个测试方法都用 `@pytest.mark.parametrize("transport_mode", ["redis", "gateway"])`
  双跑两个实现,使得契约差异立即暴露。
- 测试只接触 `TransportBase` 的公开接口(详见 `services/worker/src/antcode_worker/transport/base.py`),
  不会去摸具体实现的私有属性。
- Gateway 那一组目前依赖 P2 proto 重写后才能跑通,
  暂时全部 `@pytest.mark.skip(reason="depends on P2 proto refactor")`,
  但 fixture / parametrize 框架已经预接好,P2 落地后只需取消 skip 即可。

## 运行方式

```bash
# 1. 起一个一次性的 Redis 容器(端口 16379,不与本机默认 6379 冲突)
docker compose -f tests/contracts/docker-compose.test.yml up -d

# 2. 在仓库根目录跑契约测试
uv run pytest tests/contracts/ -v

# 3. 跑完清理
docker compose -f tests/contracts/docker-compose.test.yml down -v
```

如果 `localhost:16379` 上没有 Redis,**redis** 这一组会被自动 skip
(不会让 pipeline 红掉)。Gateway 这一组目前总是 skip。

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
