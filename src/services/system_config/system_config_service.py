"""系统配置服务"""
from datetime import datetime

from loguru import logger

from src.models.system_config import SystemConfig
from src.utils.serialization import from_json
from src.schemas.system_config import (
    SystemConfigCreate,
    SystemConfigUpdate,
    TaskResourceConfig,
    TaskLogConfig,
    SchedulerConfig,
    CacheConfig,
    MonitoringConfig,
    AllSystemConfigs
)
from src.core.config import settings


class SystemConfigService:
    """系统配置服务"""

    def __init__(self):
        """初始化配置缓存"""
        self._config_cache = {}
        self._last_reload = None

    async def get_config_by_key(self, config_key):
        """根据配置键获取配置"""
        return await SystemConfig.filter(config_key=config_key, is_active=True).first()

    async def get_all_configs(self, category=None):
        """获取所有配置"""
        query = SystemConfig.all()
        if category:
            query = query.filter(category=category)
        return await query.order_by("category", "config_key")

    async def create_config(self, config_data: SystemConfigCreate, modified_by):
        """创建配置"""
        # 检查配置键是否已存在
        existing = await SystemConfig.filter(config_key=config_data.config_key).first()
        if existing:
            raise ValueError(f"配置键 {config_data.config_key} 已存在")

        config = await SystemConfig.create(
            config_key=config_data.config_key,
            config_value=config_data.config_value,
            category=config_data.category,
            description=config_data.description,
            value_type=config_data.value_type,
            is_active=config_data.is_active,
            modified_by=modified_by
        )

        # 更新缓存
        await self.reload_config_cache()

        logger.info(f"创建系统配置: {config_data.config_key} by {modified_by}")
        return config

    async def update_config(self, config_key, config_data: SystemConfigUpdate, modified_by):
        """更新配置"""
        config = await self.get_config_by_key(config_key)
        if not config:
            raise ValueError(f"配置键 {config_key} 不存在")

        update_data = config_data.model_dump(exclude_unset=True)
        update_data["modified_by"] = modified_by

        await config.update_from_dict(update_data).save()

        # 热加载配置
        await self.reload_config_cache()

        logger.info(f"更新系统配置: {config_key} by {modified_by}")
        return config

    async def delete_config(self, config_key):
        """删除配置"""
        config = await SystemConfig.filter(config_key=config_key).first()
        if not config:
            return False

        await config.delete()

        # 更新缓存
        await self.reload_config_cache()

        logger.info(f"删除系统配置: {config_key}")
        return True

    async def batch_update_configs(self, configs, modified_by):
        """批量更新配置"""
        updated_count = 0

        for config_item in configs:
            config_key = config_item.get("config_key")
            if not config_key:
                continue

            try:
                config = await SystemConfig.filter(config_key=config_key).first()
                if config:
                    # 更新现有配置
                    config.config_value = config_item.get("config_value", config.config_value)
                    config.is_active = config_item.get("is_active", config.is_active)
                    config.modified_by = modified_by
                    await config.save()
                else:
                    # 创建新配置
                    await SystemConfig.create(
                        config_key=config_key,
                        config_value=config_item.get("config_value", ""),
                        category=config_item.get("category", "general"),
                        description=config_item.get("description"),
                        value_type=config_item.get("value_type", "string"),
                        is_active=config_item.get("is_active", True),
                        modified_by=modified_by
                    )
                updated_count += 1
            except Exception as e:
                logger.error(f"批量更新配置失败 {config_key}: {e}")
                continue

        # 热加载配置
        await self.reload_config_cache()

        logger.info(f"批量更新 {updated_count} 个配置 by {modified_by}")
        return updated_count

    async def reload_config_cache(self):
        """重新加载配置缓存（热加载）"""
        try:
            configs = await SystemConfig.filter(is_active=True).all()

            # 记录需要重启才能生效的配置变更
            restart_required_configs = []

            # 清空并重建缓存
            old_cache = self._config_cache.copy()
            self._config_cache.clear()

            for config in configs:
                try:
                    # 根据数据类型解析配置值
                    if config.value_type == "int":
                        value = int(config.config_value)
                    elif config.value_type == "float":
                        value = float(config.config_value)
                    elif config.value_type == "bool":
                        value = config.config_value.lower() in ("true", "1", "yes", "on")
                    elif config.value_type == "json":
                        value = from_json(config.config_value)
                    else:
                        value = config.config_value

                    self._config_cache[config.config_key] = value

                    # 检测需要重启的配置变更
                    if config.config_key in old_cache and old_cache[config.config_key] != value:
                        if config.config_key in ["max_concurrent_tasks", "scheduler_timezone"]:
                            restart_required_configs.append(config.config_key)

                except Exception as e:
                    logger.error(f"解析配置 {config.config_key} 失败: {e}")
                    self._config_cache[config.config_key] = config.config_value

            self._last_reload = datetime.now()
            logger.info(f"配置缓存已重新加载，共 {len(self._config_cache)} 个配置项")

            # 提示需要重启的配置
            if restart_required_configs:
                logger.warning(f"以下配置已修改但需要重启服务才能完全生效: {', '.join(restart_required_configs)}")

            # 动态更新settings对象
            self._apply_to_settings()

        except Exception as e:
            logger.error(f"重新加载配置缓存失败: {e}")

    def _apply_to_settings(self):
        """将配置应用到settings对象"""
        # 映射配置键到settings属性
        config_mapping = {
            "max_concurrent_tasks": "MAX_CONCURRENT_TASKS",
            "task_execution_timeout": "TASK_EXECUTION_TIMEOUT",
            "task_cpu_time_limit": "TASK_CPU_TIME_LIMIT_SEC",
            "task_memory_limit": "TASK_MEMORY_LIMIT_MB",
            "task_max_retries": "TASK_MAX_RETRIES",
            "task_retry_delay": "TASK_RETRY_DELAY",
            "task_log_retention_days": "TASK_LOG_RETENTION_DAYS",
            "task_log_max_size": "TASK_LOG_MAX_SIZE",
            "scheduler_timezone": "SCHEDULER_TIMEZONE",
            "cleanup_workspace_on_completion": "CLEANUP_WORKSPACE_ON_COMPLETION",
            "cleanup_workspace_max_age_hours": "CLEANUP_WORKSPACE_MAX_AGE_HOURS",
            "cache_enabled": "CACHE_ENABLED",
            "cache_default_ttl": "CACHE_DEFAULT_TTL",
            "metrics_cache_ttl": "METRICS_CACHE_TTL",
            "api_cache_ttl": "API_CACHE_TTL",
            "users_cache_ttl": "USERS_CACHE_TTL",
            "query_cache_ttl": "QUERY_CACHE_TTL",
            "metrics_background_update": "METRICS_BACKGROUND_UPDATE",
            "metrics_update_interval": "METRICS_UPDATE_INTERVAL",
            "monitoring_enabled": "MONITORING_ENABLED",
            "monitor_status_ttl": "MONITOR_STATUS_TTL",
            "monitor_history_ttl": "MONITOR_HISTORY_TTL",
            "monitor_history_keep_days": "MONITOR_HISTORY_KEEP_DAYS",
            "monitor_cluster_ttl": "MONITOR_CLUSTER_TTL",
            "monitor_stream_batch_size": "MONITOR_STREAM_BATCH_SIZE",
            "monitor_stream_interval": "MONITOR_STREAM_INTERVAL",
            "monitor_stream_maxlen": "MONITOR_STREAM_MAXLEN",
        }

        for config_key, settings_key in config_mapping.items():
            if config_key in self._config_cache:
                # 只在settings对象有该属性时才设置
                if hasattr(settings, settings_key):
                    setattr(settings, settings_key, self._config_cache[config_key])

    def get_cached_config(self, config_key, default=None):
        """从缓存获取配置值"""
        return self._config_cache.get(config_key, default)

    async def initialize_default_configs(self):
        """初始化默认配置"""
        default_configs = [
            # 任务资源配置
            {"config_key": "max_concurrent_tasks", "config_value": str(settings.MAX_CONCURRENT_TASKS), 
             "category": "task_resource", "description": "最大并发任务数", "value_type": "int"},
            {"config_key": "task_execution_timeout", "config_value": str(settings.TASK_EXECUTION_TIMEOUT), 
             "category": "task_resource", "description": "任务执行超时时间（秒）", "value_type": "int"},
            {"config_key": "task_cpu_time_limit", "config_value": str(settings.TASK_CPU_TIME_LIMIT_SEC), 
             "category": "task_resource", "description": "任务CPU时间限制（秒）", "value_type": "int"},
            {"config_key": "task_memory_limit", "config_value": str(settings.TASK_MEMORY_LIMIT_MB), 
             "category": "task_resource", "description": "任务内存限制（MB）", "value_type": "int"},
            {"config_key": "task_max_retries", "config_value": str(settings.TASK_MAX_RETRIES), 
             "category": "task_resource", "description": "任务最大重试次数", "value_type": "int"},
            {"config_key": "task_retry_delay", "config_value": str(settings.TASK_RETRY_DELAY), 
             "category": "task_resource", "description": "任务重试延迟（秒）", "value_type": "int"},

            # 任务日志配置
            {"config_key": "task_log_retention_days", "config_value": str(settings.TASK_LOG_RETENTION_DAYS), 
             "category": "task_log", "description": "日志保留天数", "value_type": "int"},
            {"config_key": "task_log_max_size", "config_value": str(settings.TASK_LOG_MAX_SIZE), 
             "category": "task_log", "description": "日志最大大小（字节）", "value_type": "int"},

            # 调度器配置
            {"config_key": "scheduler_timezone", "config_value": settings.SCHEDULER_TIMEZONE, 
             "category": "scheduler", "description": "调度器时区", "value_type": "string"},
            {"config_key": "cleanup_workspace_on_completion", "config_value": str(settings.CLEANUP_WORKSPACE_ON_COMPLETION), 
             "category": "scheduler", "description": "完成后清理工作空间", "value_type": "bool"},
            {"config_key": "cleanup_workspace_max_age_hours", "config_value": str(settings.CLEANUP_WORKSPACE_MAX_AGE_HOURS), 
             "category": "scheduler", "description": "工作空间最大保留时间（小时）", "value_type": "int"},

            # 缓存配置
            {"config_key": "cache_enabled", "config_value": str(settings.CACHE_ENABLED), 
             "category": "cache", "description": "是否启用缓存", "value_type": "bool"},
            {"config_key": "cache_default_ttl", "config_value": str(settings.CACHE_DEFAULT_TTL), 
             "category": "cache", "description": "默认缓存TTL（秒）", "value_type": "int"},
            {"config_key": "metrics_cache_ttl", "config_value": str(settings.METRICS_CACHE_TTL), 
             "category": "cache", "description": "指标缓存TTL（秒）", "value_type": "int"},
            {"config_key": "api_cache_ttl", "config_value": str(settings.API_CACHE_TTL), 
             "category": "cache", "description": "API缓存TTL（秒）", "value_type": "int"},
            {"config_key": "users_cache_ttl", "config_value": str(settings.USERS_CACHE_TTL), 
             "category": "cache", "description": "用户缓存TTL（秒）", "value_type": "int"},
            {"config_key": "query_cache_ttl", "config_value": str(settings.QUERY_CACHE_TTL), 
             "category": "cache", "description": "查询缓存TTL（秒）", "value_type": "int"},
            {"config_key": "metrics_background_update", "config_value": str(settings.METRICS_BACKGROUND_UPDATE), 
             "category": "cache", "description": "是否启用指标后台更新", "value_type": "bool"},
            {"config_key": "metrics_update_interval", "config_value": str(settings.METRICS_UPDATE_INTERVAL), 
             "category": "cache", "description": "指标更新间隔（秒）", "value_type": "int"},

            # 监控配置
            {"config_key": "monitoring_enabled", "config_value": str(settings.MONITORING_ENABLED), 
             "category": "monitoring", "description": "是否启用监控", "value_type": "bool"},
            {"config_key": "monitor_status_ttl", "config_value": str(settings.MONITOR_STATUS_TTL), 
             "category": "monitoring", "description": "监控状态TTL（秒）", "value_type": "int"},
            {"config_key": "monitor_history_ttl", "config_value": str(settings.MONITOR_HISTORY_TTL), 
             "category": "monitoring", "description": "监控历史TTL（秒）", "value_type": "int"},
            {"config_key": "monitor_history_keep_days", "config_value": str(settings.MONITOR_HISTORY_KEEP_DAYS), 
             "category": "monitoring", "description": "监控历史保留天数", "value_type": "int"},
            {"config_key": "monitor_cluster_ttl", "config_value": str(settings.MONITOR_CLUSTER_TTL), 
             "category": "monitoring", "description": "集群状态TTL（秒）", "value_type": "int"},
            {"config_key": "monitor_stream_batch_size", "config_value": str(settings.MONITOR_STREAM_BATCH_SIZE), 
             "category": "monitoring", "description": "监控流批处理大小", "value_type": "int"},
            {"config_key": "monitor_stream_interval", "config_value": str(settings.MONITOR_STREAM_INTERVAL), 
             "category": "monitoring", "description": "监控流处理间隔（秒）", "value_type": "int"},
            {"config_key": "monitor_stream_maxlen", "config_value": str(settings.MONITOR_STREAM_MAXLEN), 
             "category": "monitoring", "description": "监控流最大长度", "value_type": "int"},
        ]

        for config_data in default_configs:
            existing = await SystemConfig.filter(config_key=config_data["config_key"]).first()
            if not existing:
                await SystemConfig.create(**config_data, modified_by="system")

        # 加载到缓存
        await self.reload_config_cache()

        logger.info("默认系统配置已初始化")

    async def get_all_configs_by_category(self):
        """按分类获取所有配置"""
        configs = await self.get_all_configs()

        # 按分类组织配置
        config_dict = {}
        for config in configs:
            if config.category not in config_dict:
                config_dict[config.category] = {}

            # 解析配置值
            try:
                if config.value_type == "int":
                    value = int(config.config_value)
                elif config.value_type == "float":
                    value = float(config.config_value)
                elif config.value_type == "bool":
                    value = config.config_value.lower() in ("true", "1", "yes", "on")
                elif config.value_type == "json":
                    value = from_json(config.config_value)
                else:
                    value = config.config_value

                config_dict[config.category][config.config_key] = value
            except Exception as e:
                logger.error(f"解析配置 {config.config_key} 失败: {e}")
                config_dict[config.category][config.config_key] = config.config_value

        # 构建响应
        return AllSystemConfigs(
            task_resource=TaskResourceConfig(**config_dict.get("task_resource", {})),
            task_log=TaskLogConfig(**config_dict.get("task_log", {})),
            scheduler=SchedulerConfig(**config_dict.get("scheduler", {})),
            cache=CacheConfig(**config_dict.get("cache", {})),
            monitoring=MonitoringConfig(**config_dict.get("monitoring", {}))
        )


# 全局服务实例
system_config_service = SystemConfigService()

