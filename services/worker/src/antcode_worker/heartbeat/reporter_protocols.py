"""心跳上报器依赖的协作方协议。

与实现分开声明：``TransportProtocol`` 把 ``LeaseRenewSource`` 并入强制契约，
服务端续期节拍必须能从传输层读回，否则节拍会永远停在启动协商值（B6）。
"""

from __future__ import annotations

from typing import Any, Protocol

from antcode_worker.heartbeat.lease_timing import LeaseRenewSource


class TransportProtocol(LeaseRenewSource, Protocol):
    """传输层协议。

    ``LeaseRenewSource`` 是强制契约的一部分：服务端续期节拍必须能从传输层
    读回，否则节拍会永远停在启动协商值（B6）。
    """

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        ...

    async def send_heartbeat(self, heartbeat: Any) -> bool:
        """发送心跳"""
        ...

    async def reconnect(self) -> bool:
        """重连"""
        ...


class MetricsCollectorProtocol(Protocol):
    """指标收集器协议"""

    async def collect(self, use_cache: bool = True) -> Any:
        """异步采集系统与 Worker 指标"""
        ...

    def get_os_info(self) -> dict:
        """获取操作系统信息"""
        ...

    def get_spider_stats(self) -> dict | None:
        """获取爬虫统计"""
        ...

    def update_heartbeat_ts(self, ts: float | None = None) -> None:
        """更新心跳时间戳"""
        ...

    def increment_reconnect_count(self) -> None:
        """增加重连计数"""
        ...

    def reset_reconnect_count(self) -> None:
        """重置重连计数"""
        ...


__all__ = ["MetricsCollectorProtocol", "TransportProtocol"]
