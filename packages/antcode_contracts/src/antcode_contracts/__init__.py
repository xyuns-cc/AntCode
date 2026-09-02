"""
AntCode Contracts - gRPC 契约包

此包包含从 contracts/proto/ 目录生成的 Python gRPC 代码。
作为单一真相来源，所有服务应从此包导入 gRPC 相关类型。

生成命令：
    ./scripts/gen_proto.sh

包含的 proto 文件：
    - common.proto:  通用消息定义（Timestamp, Metrics, OSInfo, TraceContext, AuditEvent 等）
    - control.proto: ControlService 控制面（Register / Lease / CancelTask / WatchControl）
    - data.proto:    DataService 数据面（StreamTasks / StreamStatus / StreamLogs）
    - artifact.proto: ArtifactService（source bundle 下载 / task artifact 上传）

使用示例：
    from antcode_contracts import artifact_pb2, common_pb2, control_pb2, data_pb2
    from antcode_contracts import artifact_pb2_grpc, control_pb2_grpc, data_pb2_grpc
"""

# 导出将在 proto 生成后可用
__all__ = [
    "artifact_pb2",
    "artifact_pb2_grpc",
    "common_pb2",
    "common_pb2_grpc",
    "control_pb2",
    "control_pb2_grpc",
    "data_pb2",
    "data_pb2_grpc",
    # 共享的 Proto <-> Python 类型转换工具（见 transcode.py）
    "transcode",
]


def _lazy_import(name: str):
    """延迟导入，避免在 proto 未生成时报错"""
    import importlib

    try:
        return importlib.import_module(f".{name}", __package__)
    except ImportError as e:
        raise ImportError(f"无法导入 {name}。请先运行 proto 生成脚本：./scripts/gen_proto.sh") from e


def __getattr__(name: str):
    """支持延迟导入 proto 生成的模块"""
    if name in __all__:
        return _lazy_import(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
