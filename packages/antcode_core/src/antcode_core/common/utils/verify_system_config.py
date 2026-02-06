"""系统配置验证脚本"""

import asyncio

from loguru import logger
from tortoise import Tortoise

from antcode_core.common.config import settings
from antcode_core.infrastructure.db.tortoise import get_default_tortoise_config


async def verify_config():
    """验证系统配置是否正确设置和应用"""
    try:
        # 初始化数据库连接
        await Tortoise.init(config=get_default_tortoise_config())

        # 导入服务
        from antcode_core.application.services.system_config import system_config_service

        logger.info("=" * 60)
        logger.info("系统配置验证")
        logger.info("=" * 60)

        # 1. 验证配置缓存
        logger.info("\n[1] 验证配置缓存")
        await system_config_service.reload_config_cache()
        cache_size = len(system_config_service._config_cache)
        logger.info(f"OK 配置缓存已加载: {cache_size} 个配置项")

        # 2. 验证settings对象
        logger.info("\n[2] 验证settings对象属性")
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
            "monitoring_enabled": "MONITORING_ENABLED",
            "monitor_status_ttl": "MONITOR_STATUS_TTL",
            "monitor_history_ttl": "MONITOR_HISTORY_TTL",
            "monitor_history_keep_days": "MONITOR_HISTORY_KEEP_DAYS",
        }

        missing_attrs = []
        for config_key, settings_key in config_mapping.items():
            if not hasattr(settings, settings_key):
                missing_attrs.append(settings_key)
                logger.error(f"FAIL settings 缺少属性: {settings_key}")
            else:
                value = getattr(settings, settings_key)
                cached_value = system_config_service.get_cached_config(config_key)
                if cached_value is not None and value == cached_value:
                    logger.info(f"OK {settings_key} = {value} (已同步)")
                else:
                    logger.warning(
                        f"WARN {settings_key} = {value}, 缓存值 = {cached_value} (不同步)"
                    )

        if missing_attrs:
            logger.error(f"\n缺少 {len(missing_attrs)} 个settings属性: {', '.join(missing_attrs)}")
        else:
            logger.info("\nOK 所有 settings 属性都已正确配置")

        # 3. 验证配置类型
        logger.info("\n[3] 验证配置类型")
        from antcode_core.domain.models.system_config import SystemConfig

        configs = await SystemConfig.filter(is_active=True).all()
        type_errors = []

        for config in configs:
            try:
                if config.value_type == "int":
                    int(config.config_value)
                elif config.value_type == "float":
                    float(config.config_value)
                elif config.value_type == "bool":
                    assert config.config_value.lower() in (
                        "true",
                        "false",
                        "1",
                        "0",
                        "yes",
                        "no",
                        "on",
                        "off",
                    )
                logger.info(
                    f"OK {config.config_key}: {config.value_type} = {config.config_value}"
                )
            except Exception as e:
                type_errors.append((config.config_key, str(e)))
                logger.error(f"FAIL {config.config_key}: 类型错误 - {e}")

        if type_errors:
            logger.error(f"\n发现 {len(type_errors)} 个类型错误")
        else:
            logger.info("\nOK 所有配置类型验证通过")

        # 4. 验证配置范围
        logger.info("\n[4] 验证配置值范围")
        range_checks = {
            "max_concurrent_tasks": (1, 100),
            "task_execution_timeout": (60, 86400),
            "task_cpu_time_limit": (60, 3600),
            "task_memory_limit": (128, 8192),
            "task_max_retries": (0, 10),
            "task_retry_delay": (10, 600),
            "task_log_retention_days": (1, 365),
            "task_log_max_size": (1048576, 1073741824),
            "cleanup_workspace_max_age_hours": (1, 168),
            "cache_default_ttl": (60, 3600),
            "metrics_cache_ttl": (10, 300),
            "api_cache_ttl": (60, 3600),
            "monitor_status_ttl": (60, 3600),
            "monitor_history_ttl": (600, 86400),
            "monitor_history_keep_days": (1, 365),
        }

        range_errors = []
        for config_key, (min_val, max_val) in range_checks.items():
            value = system_config_service.get_cached_config(config_key)
            if value is not None:
                if isinstance(value, (int, float)) and (value < min_val or value > max_val):
                    range_errors.append((config_key, value, min_val, max_val))
                    logger.error(
                        f"FAIL {config_key} = {value} (超出范围 {min_val}-{max_val})"
                    )
                else:
                    logger.info(f"OK {config_key} = {value} (范围正常)")

        if range_errors:
            logger.error(f"\n发现 {len(range_errors)} 个范围错误")
        else:
            logger.info("\nOK 所有配置值范围验证通过")

        # 5. 总结
        logger.info("\n" + "=" * 60)
        total_issues = len(missing_attrs) + len(type_errors) + len(range_errors)
        if total_issues == 0:
            logger.info("[OK] 系统配置验证通过，所有配置正常")
        else:
            logger.error(f"[Error] 发现 {total_issues} 个问题需要修复")
            logger.error(f"   - 缺少属性: {len(missing_attrs)}")
            logger.error(f"   - 类型错误: {len(type_errors)}")
            logger.error(f"   - 范围错误: {len(range_errors)}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"验证失败: {e}")
        raise
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(verify_config())
