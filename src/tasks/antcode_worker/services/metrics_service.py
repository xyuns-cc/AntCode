"""
指标服务

负责收集和提供系统指标。

Requirements: 11.3
"""

import platform
from datetime import datetime
from typing import Dict, Any, Optional

import psutil
from loguru import logger

from ..domain.interfaces import MetricsService as IMetricsService
from ..transport.protocol import TransportProtocol


class MetricsServiceImpl(IMetricsService):
    """
    指标服务实现
    
    特性:
    - 系统指标收集（CPU、内存、磁盘）
    - 操作系统信息
    - 通信指标统计
    - 任务统计
    
    Requirements: 11.3
    """

    def __init__(self, transport: Optional[TransportProtocol] = None):
        """
        初始化指标服务
        
        Args:
            transport: 传输协议实例（用于获取通信指标）
        """
        self._transport = transport
        self._os_info_cache: Optional[Dict[str, str]] = None
        self._start_time = datetime.now()

    def set_transport(self, transport: TransportProtocol):
        """设置传输协议实例"""
        self._transport = transport

    def get_system_metrics(self) -> Dict[str, Any]:
        """获取系统指标"""
        try:
            memory_info = psutil.virtual_memory()
            disk_info = psutil.disk_usage("/")

            # 尝试获取任务统计
            running_tasks = 0
            task_count = 0
            max_concurrent = 5
            project_count = 0
            env_count = 0

            try:
                from ..config import get_node_config
                from ..api.deps import get_engine
                from .project_service import local_project_service
                from .env_service import local_env_service

                config = get_node_config()
                max_concurrent = config.max_concurrent_tasks if config else 5

                engine = get_engine()
                stats = engine.get_stats()
                task_count = stats.get("tasks_received", 0)
                running_tasks = stats.get("executor", {}).get("running", 0)

                project_count = len(local_project_service._projects)
                env_count = local_env_service.get_env_count()
            except Exception:
                pass

            return {
                "cpu": round(psutil.cpu_percent(interval=0.1), 1),
                "memory": round(memory_info.percent, 1),
                "disk": round(disk_info.percent, 1),
                "taskCount": task_count,
                "runningTasks": running_tasks,
                "maxConcurrentTasks": max_concurrent,
                "projectCount": project_count,
                "envCount": env_count,
                "uptime": self._get_uptime(),
                "cpuCores": psutil.cpu_count(),
                "memoryTotal": memory_info.total,
                "memoryUsed": memory_info.used,
                "memoryAvailable": memory_info.available,
                "diskTotal": disk_info.total,
                "diskUsed": disk_info.used,
                "diskFree": disk_info.free,
            }
        except Exception as e:
            logger.warning(f"获取系统指标异常: {e}")
            return {
                "cpu": 0,
                "memory": 0,
                "disk": 0,
                "taskCount": 0,
                "runningTasks": 0,
                "maxConcurrentTasks": 5,
                "projectCount": 0,
                "envCount": 0,
                "uptime": 0,
            }

    def get_os_info(self) -> Dict[str, str]:
        """获取操作系统信息"""
        if self._os_info_cache is None:
            self._os_info_cache = {
                "os_type": platform.system(),
                "os_version": platform.release(),
                "python_version": platform.python_version(),
                "machine_arch": platform.machine(),
            }
        return self._os_info_cache

    def get_communication_metrics(self) -> Dict[str, Any]:
        """获取通信指标"""
        if not self._transport:
            return {
                "protocol": "none",
                "connected": False,
                "messages_sent": 0,
                "messages_received": 0,
                "avg_latency_ms": 0,
            }

        metrics = self._transport.metrics
        return {
            "protocol": self._transport.protocol_name,
            "connected": self._transport.is_connected,
            "messages_sent": metrics.messages_sent,
            "messages_received": metrics.messages_received,
            "bytes_sent": metrics.bytes_sent,
            "bytes_received": metrics.bytes_received,
            "reconnect_count": metrics.reconnect_count,
            "avg_latency_ms": round(metrics.avg_latency_ms, 2),
            "connected_at": metrics.connected_at.isoformat() if metrics.connected_at else None,
        }

    def get_all_metrics(self) -> Dict[str, Any]:
        """获取所有指标"""
        return {
            "system": self.get_system_metrics(),
            "os": self.get_os_info(),
            "communication": self.get_communication_metrics(),
            "timestamp": datetime.now().isoformat(),
        }

    def _get_uptime(self) -> int:
        """获取运行时间（秒）"""
        try:
            from ..config import get_node_config
            config = get_node_config()
            if config.start_time:
                return int((datetime.now() - config.start_time).total_seconds())
        except Exception:
            pass
        return int((datetime.now() - self._start_time).total_seconds())

    def get_node_info(self) -> Dict[str, Any]:
        """获取完整的节点信息"""
        try:
            from ..config import get_node_config
            config = get_node_config()

            return {
                "name": config.name,
                "host": config.host,
                "port": config.port,
                "region": config.region,
                "machine_code": config.machine_code,
                "version": config.version,
                "is_connected": self._transport.is_connected if self._transport else False,
                "start_time": config.start_time.isoformat(),
                "metrics": self.get_system_metrics(),
                "system": {
                    **self.get_os_info(),
                    "cpu_count": psutil.cpu_count(),
                    "memory_total": psutil.virtual_memory().total,
                },
                "communication": self.get_communication_metrics(),
            }
        except Exception as e:
            logger.warning(f"获取节点信息异常: {e}")
            return {
                "error": str(e),
                "metrics": self.get_system_metrics(),
                "system": self.get_os_info(),
            }
