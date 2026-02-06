# AntCode Contracts

gRPC 契约包 - 包含从 `contracts/proto/` 生成的 Python 代码。

## 概述

此包是 AntCode 项目中 gRPC 通信的单一真相来源。所有服务应从此包导入 gRPC 相关类型，而不是自行维护 proto 文件或生成代码。

## Proto 文件

| 文件 | 描述 |
|------|------|
| `common.proto` | 通用消息定义（Timestamp, Metrics, OSInfo 等） |
| `gateway.proto` | Gateway 服务定义（公网 Worker 接入） |

## 生成代码

运行以下命令生成 Python 代码：

```bash
./scripts/gen_proto.sh
```

## 使用示例

```python
from antcode_contracts import common_pb2, gateway_pb2
from antcode_contracts import gateway_pb2_grpc

# 创建消息
metrics = common_pb2.Metrics(cpu=50.0, memory=60.0)
heartbeat = gateway_pb2.Heartbeat(worker_id="worker-1", metrics=metrics)
```

## 依赖

- `grpcio>=1.60.0`
- `protobuf>=4.25.0`
