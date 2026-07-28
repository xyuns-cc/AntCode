"""
gRPC 服务实现 (P1c)

提供 ControlService、DataService 和 ArtifactService：

- ``GatewayControlService`` — Register/Deregister/Lease/CancelTask/UpdateConfig
  + WatchControl (server-stream) + AckControl
- ``GatewayDataService`` — StreamTasks (server-stream) / AckTask
  + StreamStatus / StreamLogs (client-stream)
- ``GatewayArtifactService`` — source bundle 下载 + task artifact 上传

旧 ``GatewayServiceImpl`` 单 service 已删除。
"""

from antcode_gateway.services.artifact_service import GatewayArtifactService
from antcode_gateway.services.control_service import GatewayControlService
from antcode_gateway.services.data_service import GatewayDataService

__all__ = [
    "GatewayArtifactService",
    "GatewayControlService",
    "GatewayDataService",
]
