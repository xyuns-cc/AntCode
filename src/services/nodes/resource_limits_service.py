"""资源限制配置服务

提供全局默认资源限制和节点级自定义配置:
- 全局默认配置
- 节点级覆盖
- 任务级覆盖
"""
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

from loguru import logger

from src.core.config import settings


@dataclass
class ResourceLimitsConfig:
    """资源限制配置"""
    cpu_time: int = 3600           # CPU 时间限制 (秒)
    wall_time: int = 7200          # 墙钟时间限制 (秒)
    memory_mb: int = 512           # 内存限制 (MB)
    disk_mb: int = 1024            # 磁盘写入限制 (MB)
    file_size_mb: int = 100        # 单文件大小限制 (MB)
    max_processes: int = 50        # 最大进程数
    max_open_files: int = 1024     # 最大打开文件数
    max_output_lines: int = 100000 # 最大输出行数
    enable_security_scan: bool = True  # 是否启用安全扫描

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ResourceLimitsConfig':
        """从字典创建，只取有效字段"""
        valid_fields = {
            'cpu_time', 'wall_time', 'memory_mb', 'disk_mb',
            'file_size_mb', 'max_processes', 'max_open_files',
            'max_output_lines', 'enable_security_scan'
        }
        filtered = {k: v for k, v in data.items() if k in valid_fields and v is not None}
        return cls(**filtered)

    def merge_with(self, override: Dict[str, Any]) -> 'ResourceLimitsConfig':
        """合并覆盖配置，返回新实例"""
        current = self.to_dict()
        for key, value in override.items():
            if key in current and value is not None:
                current[key] = value
        return ResourceLimitsConfig.from_dict(current)


class ResourceLimitsService:
    """资源限制服务
    
    优先级（从高到低）：
    1. 任务级配置 (task.execution_params.resource_limits)
    2. 节点级配置 (node.resource_limits)
    3. 全局默认配置 (settings 或系统配置)
    """

    # 全局默认配置
    _global_defaults: Optional[ResourceLimitsConfig] = None

    @classmethod
    def get_global_defaults(cls) -> ResourceLimitsConfig:
        """获取全局默认资源限制"""
        if cls._global_defaults is None:
            cls._global_defaults = ResourceLimitsConfig(
                cpu_time=getattr(settings, 'TASK_CPU_TIME_LIMIT', 3600),
                wall_time=getattr(settings, 'TASK_WALL_TIME_LIMIT', 7200),
                memory_mb=getattr(settings, 'TASK_MEMORY_LIMIT_MB', 512),
                disk_mb=getattr(settings, 'TASK_DISK_LIMIT_MB', 1024),
                file_size_mb=getattr(settings, 'TASK_FILE_SIZE_LIMIT_MB', 100),
                max_processes=getattr(settings, 'TASK_MAX_PROCESSES', 50),
                max_open_files=getattr(settings, 'TASK_MAX_OPEN_FILES', 1024),
                max_output_lines=getattr(settings, 'TASK_MAX_OUTPUT_LINES', 100000),
                enable_security_scan=getattr(settings, 'TASK_ENABLE_SECURITY_SCAN', True),
            )
        return cls._global_defaults

    @classmethod
    def set_global_defaults(cls, config: ResourceLimitsConfig):
        """设置全局默认资源限制"""
        cls._global_defaults = config
        logger.info(f"全局资源限制已更新: {config.to_dict()}")

    @classmethod
    async def get_limits_for_node(cls, node_id: int) -> ResourceLimitsConfig:
        """获取节点的资源限制配置
        
        优先使用节点自定义配置，否则使用全局默认
        """
        from src.models import Node

        defaults = cls.get_global_defaults()

        try:
            node = await Node.get_or_none(id=node_id)
            if node and node.resource_limits:
                return defaults.merge_with(node.resource_limits)
        except Exception as e:
            logger.warning(f"获取节点资源限制失败: {e}")

        return defaults

    @classmethod
    async def get_limits_for_task(
        cls, 
        node_id: Optional[int] = None,
        task_limits: Optional[Dict[str, Any]] = None
    ) -> ResourceLimitsConfig:
        """获取任务的资源限制配置
        
        优先级: 任务级 > 节点级 > 全局默认
        """
        # 从节点或全局获取基础配置
        if node_id:
            base_config = await cls.get_limits_for_node(node_id)
        else:
            base_config = cls.get_global_defaults()

        # 应用任务级覆盖
        if task_limits:
            return base_config.merge_with(task_limits)

        return base_config

    @classmethod
    async def update_node_limits(
        cls,
        node_id: int,
        limits: Dict[str, Any]
    ) -> bool:
        """更新节点的资源限制配置"""
        from src.models import Node

        try:
            node = await Node.get_or_none(id=node_id)
            if not node:
                logger.warning(f"节点不存在: {node_id}")
                return False

            # 合并现有配置
            current_limits = node.resource_limits or {}
            current_limits.update(limits)

            # 验证配置有效性
            ResourceLimitsConfig.from_dict(current_limits)

            node.resource_limits = current_limits
            await node.save(update_fields=['resource_limits'])

            logger.info(f"节点 {node.name} 资源限制已更新: {current_limits}")
            return True

        except Exception as e:
            logger.error(f"更新节点资源限制失败: {e}")
            return False

    @classmethod
    async def clear_node_limits(cls, node_id: int) -> bool:
        """清除节点的自定义资源限制（使用全局默认）"""
        from src.models import Node

        try:
            node = await Node.get_or_none(id=node_id)
            if not node:
                return False

            node.resource_limits = {}
            await node.save(update_fields=['resource_limits'])

            logger.info(f"节点 {node.name} 资源限制已清除，使用全局默认")
            return True

        except Exception as e:
            logger.error(f"清除节点资源限制失败: {e}")
            return False


# 全局服务实例
resource_limits_service = ResourceLimitsService()
