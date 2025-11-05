"""
数据库索引优化脚本
为常用查询添加索引以提升性能
"""

from loguru import logger
from tortoise import Tortoise


async def create_performance_indexes():
    """创建性能优化索引"""
    
    connection = Tortoise.get_connection("default")
    
    # 索引创建语句
    indexes = [
        # 项目表索引 (projects表已有部分索引，只添加缺失的)
        "CREATE INDEX IF NOT EXISTS idx_projects_user_type ON projects (user_id, type);",
        "CREATE INDEX IF NOT EXISTS idx_projects_user_status ON projects (user_id, status);",
        "CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects (created_at);",
        "CREATE INDEX IF NOT EXISTS idx_projects_user_type_status ON projects (user_id, type, status);",
        "CREATE INDEX IF NOT EXISTS idx_projects_user_created ON projects (user_id, created_at DESC);",
        "CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects (updated_at);",
        "CREATE INDEX IF NOT EXISTS idx_projects_star_count ON projects (star_count DESC);",
        "CREATE INDEX IF NOT EXISTS idx_projects_download_count ON projects (download_count DESC);",
        
        # 用户表索引 (users表可能已有username索引)
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);",
        "CREATE INDEX IF NOT EXISTS idx_users_is_active ON users (is_active);",
        "CREATE INDEX IF NOT EXISTS idx_users_is_admin ON users (is_admin);",
        "CREATE INDEX IF NOT EXISTS idx_users_created_at ON users (created_at);",
        "CREATE INDEX IF NOT EXISTS idx_users_last_login_at ON users (last_login_at);",
        "CREATE INDEX IF NOT EXISTS idx_users_active_admin ON users (is_active, is_admin);",
        "CREATE INDEX IF NOT EXISTS idx_users_active_created ON users (is_active, created_at DESC);",
        
        # 项目文件表索引  
        "CREATE INDEX IF NOT EXISTS idx_project_files_project_id ON project_files (project_id);",
        "CREATE INDEX IF NOT EXISTS idx_project_files_hash ON project_files (file_hash);",
        "CREATE INDEX IF NOT EXISTS idx_project_files_type ON project_files (file_type);",
        "CREATE INDEX IF NOT EXISTS idx_project_files_size ON project_files (file_size);",
        "CREATE INDEX IF NOT EXISTS idx_project_files_created_at ON project_files (created_at);",
        "CREATE INDEX IF NOT EXISTS idx_project_files_project_type ON project_files (project_id, file_type);",
        
        # 项目规则表索引
        "CREATE INDEX IF NOT EXISTS idx_project_rules_project_id ON project_rules (project_id);",
        "CREATE INDEX IF NOT EXISTS idx_project_rules_engine ON project_rules (engine);",
        "CREATE INDEX IF NOT EXISTS idx_project_rules_callback_type ON project_rules (callback_type);",
        "CREATE INDEX IF NOT EXISTS idx_project_rules_request_method ON project_rules (request_method);",
        "CREATE INDEX IF NOT EXISTS idx_project_rules_created_at ON project_rules (created_at);",
        
        # 项目代码表索引
        "CREATE INDEX IF NOT EXISTS idx_project_codes_project_id ON project_codes (project_id);",
        "CREATE INDEX IF NOT EXISTS idx_project_codes_language ON project_codes (language);",
        "CREATE INDEX IF NOT EXISTS idx_project_codes_hash ON project_codes (content_hash);",
        "CREATE INDEX IF NOT EXISTS idx_project_codes_created_at ON project_codes (created_at);",
        
        # 调度任务表索引
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project_id ON scheduled_tasks (project_id);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_is_active ON scheduled_tasks (is_active);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_status ON scheduled_tasks (status);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_schedule_type ON scheduled_tasks (schedule_type);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run_time ON scheduled_tasks (next_run_time);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_created_at ON scheduled_tasks (created_at);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_active_status ON scheduled_tasks (is_active, status);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project_active ON scheduled_tasks (project_id, is_active);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user_id ON scheduled_tasks (user_id);",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_user_active ON scheduled_tasks (user_id, is_active);",
        
        # 任务执行表索引
        "CREATE INDEX IF NOT EXISTS idx_task_executions_task_id ON task_executions (task_id);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_status ON task_executions (status);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_start_time ON task_executions (start_time);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_end_time ON task_executions (end_time);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_execution_id ON task_executions (execution_id);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_task_status ON task_executions (task_id, status);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_task_start ON task_executions (task_id, start_time DESC);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_created_at ON task_executions (created_at);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_user_id ON task_executions (user_id);",
        "CREATE INDEX IF NOT EXISTS idx_task_executions_user_status ON task_executions (user_id, status);",
    ]
    
    success_count = 0
    error_count = 0
    
    logger.info("开始创建性能优化索引...")
    
    for index_sql in indexes:
        try:
            await connection.execute_query(index_sql)
            success_count += 1
            
            # 提取索引名用于日志
            index_name = index_sql.split("idx_")[1].split(" ")[0] if "idx_" in index_sql else "unknown"
            logger.debug(f"创建索引成功: {index_name}")
            
        except Exception as e:
            error_count += 1
            logger.error(f"创建索引失败: {index_sql} - {e}")
    
    logger.info(f"索引创建完成: 成功 {success_count} 个, 失败 {error_count} 个")
    
    return success_count, error_count


async def analyze_table_stats():
    """分析表统计信息"""
    
    connection = Tortoise.get_connection("default")
    
    tables_to_analyze = [
        'projects', 'users', 'project_files', 'project_rules', 'project_codes',
        'scheduled_tasks', 'task_executions'
    ]
    
    stats = {}
    
    for table in tables_to_analyze:
        try:
            # 获取表行数
            result = await connection.execute_query(f"SELECT COUNT(*) as count FROM {table}")
            count = result[0]['count'] if result else 0
            
            # 获取表大小信息 (SQLite特定)
            try:
                size_result = await connection.execute_query(
                    f"SELECT page_count * page_size as size FROM pragma_page_count('{table}'), pragma_page_size"
                )
                size = size_result[0]['size'] if size_result else 0
            except:
                size = 0
            
            stats[table] = {
                'count': count,
                'size_bytes': size,
                'size_mb': round(size / 1024 / 1024, 2) if size > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"获取表 {table} 统计信息失败: {e}")
            stats[table] = {'error': str(e)}
    
    # 记录统计信息
    logger.info("数据库表统计信息:")
    for table, info in stats.items():
        if 'error' not in info:
            logger.info(f"  {table}: {info['count']} 行, {info['size_mb']} MB")
        else:
            logger.error(f"  {table}: 错误 - {info['error']}")
    
    return stats


async def optimize_database():
    """执行数据库优化"""
    
    logger.info("开始数据库优化...")
    
    try:
        # 1. 创建索引
        success_indexes, failed_indexes = await create_performance_indexes()
        
        # 2. 分析表统计信息
        table_stats = await analyze_table_stats()
        
        # 3. SQLite特定优化
        connection = Tortoise.get_connection("default")
        
        # 优化SQLite设置
        optimizations = [
            "PRAGMA optimize;",  # 优化查询计划
            "PRAGMA analysis_limit=1000;",  # 分析限制
            "PRAGMA cache_size=-2000;",  # 设置缓存大小为2MB
            "PRAGMA temp_store=memory;",  # 临时数据存储在内存
            "PRAGMA journal_mode=WAL;",  # 使用WAL模式
            "PRAGMA synchronous=NORMAL;",  # 设置同步模式
        ]
        
        for opt_sql in optimizations:
            try:
                await connection.execute_query(opt_sql)
                logger.debug(f"应用优化设置: {opt_sql}")
            except Exception as e:
                logger.warning(f"优化设置失败: {opt_sql} - {e}")
        
        logger.info("数据库优化完成!")
        logger.info(f"索引创建: {success_indexes} 成功, {failed_indexes} 失败")
        
        return {
            'success': True,
            'indexes_created': success_indexes,
            'indexes_failed': failed_indexes,
            'table_stats': table_stats
        }
        
    except Exception as e:
        logger.error(f"数据库优化失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }


if __name__ == "__main__":
    import asyncio
    from src.core.config import settings
    
    async def main():
        # 初始化Tortoise ORM
        await Tortoise.init(config=settings.TORTOISE_ORM)
        
        # 执行优化
        result = await optimize_database()
        
        print("优化结果:", result)
        
        # 关闭连接
        await Tortoise.close_connections()
    
    asyncio.run(main())