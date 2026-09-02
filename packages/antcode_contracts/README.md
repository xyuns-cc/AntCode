# AntCode Contracts

gRPC 契约包 - 包含从 `contracts/proto/` 生成的 Python 代码。

## 概述

此包是 AntCode 项目中 gRPC 通信的单一真相来源。所有服务应从此包导入 gRPC 相关类型，而不是自行维护 proto 文件或生成代码。

## Proto 文件

| 文件 | 描述 |
|------|------|
| `common.proto` | 通用消息定义（Timestamp, Metrics, OSInfo, TraceContext, AuditEvent 等） |
| `control.proto` | ControlService 控制面（Register / Lease / CancelTask / WatchControl） |
| `data.proto` | DataService 数据面（StreamTasks / StreamStatus / StreamLogs / StreamSpiderData） |
| `artifact.proto` | ArtifactService（source bundle 下载 / task artifact 上传） |

## 生成代码

```bash
./scripts/gen_proto.sh
```

薄封装，实际执行 `scripts/generate_proto.py`（同时修 `.py` 与 `.pyi` 的相对导入）。

## 使用示例

```python
from antcode_contracts import common_pb2, control_pb2, control_pb2_grpc

metrics = common_pb2.Metrics(cpu=50.0, memory=60.0)
```

## 依赖

- `grpcio>=1.76.0` — 生成桩里 `GRPC_GENERATED_VERSION = '1.76.0'`，低于该版本 import 直接 RuntimeError
- `protobuf>=6.33.5`
