"""
gRPC 服务实现 (P1c)

提供新的 ControlService + DataService 二分实现：

- ``GatewayControlService`` — Register/Deregister/Lease/CancelTask/UpdateConfig
  + WatchControl (server-stream) + AckControl
- ``GatewayDataService`` — StreamTasks (server-stream) / AckTask
  + StreamStatus / StreamLogs (client-stream)

旧 ``GatewayServiceImpl`` 单 service 已删除。
"""

from antcode_gateway.services.control_service import GatewayControlService
from antcode_gateway.services.data_service import GatewayDataService

__all__ = [
    "GatewayControlService",
    "GatewayDataService",
]
