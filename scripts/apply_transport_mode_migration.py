#!/usr/bin/env python3
"""
直接执行 SQL 添加 workers 表的 transport_mode 列
用于区分 Gateway 和 Direct 模式的 Worker

使用方法:
    uv run python scripts/apply_transport_mode_migration.py
"""

import asyncio
import sys
from pathlib import Path

from loguru import logger

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def get_tortoise_config():
    """获取 Tortoise ORM 配置"""
    try:
        from antcode_core.infrastructure.db.tortoise import TORTOISE_ORM
        return TORTOISE_ORM
    except ImportError:
        from src.core.db_config import TORTOISE_ORM
        return TORTOISE_ORM


# SQL 语句
SQL_ADD_COLUMN = """
ALTER TABLE `workers` ADD COLUMN `transport_mode` VARCHAR(20) DEFAULT 'gateway';
"""

# Direct Worker 识别条件:
# 1. public_id 以 'w-' 开头（本地身份管理器生成的格式）
# 2. host 为空或等于 'direct'
SQL_UPDATE_DIRECT = """
UPDATE `workers` SET `transport_mode` = 'direct' 
WHERE `public_id` LIKE 'w-%' 
   OR `host` = 'direct' 
   OR `host` = '' 
   OR `host` IS NULL;
"""


async def main():
    """执行数据库迁移"""
    from tortoise import Tortoise
    
    logger.info("初始化数据库连接...")
    
    try:
        # 初始化数据库连接
        config = get_tortoise_config()
        await Tortoise.init(config=config)
        
        # 获取数据库连接
        conn = Tortoise.get_connection("default")
        
        # 检查列是否已存在
        logger.info("检查 transport_mode 列是否存在...")
        try:
            result = await conn.execute_query(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = 'workers' AND COLUMN_NAME = 'transport_mode'"
            )
            column_exists = len(result[1]) > 0
        except Exception:
            column_exists = False
        
        if column_exists:
            logger.info("⚠ transport_mode 列已存在，跳过添加")
        else:
            # 添加列
            logger.info("添加 transport_mode 列到 workers 表...")
            await conn.execute_script(SQL_ADD_COLUMN)
            logger.info("✓ 列添加成功")
        
        # 更新现有 Direct Worker
        # Direct Worker 识别条件: public_id 以 'w-' 开头
        logger.info("识别并更新现有 Direct Worker...")
        await conn.execute_script(SQL_UPDATE_DIRECT)
        logger.info("✓ 现有记录更新成功")
        
        # 显示统计
        result = await conn.execute_query(
            "SELECT transport_mode, COUNT(*) as cnt FROM workers GROUP BY transport_mode"
        )
        logger.info("Worker 统计:")
        for row in result[1]:
            logger.info(f"  - {row['transport_mode'] or 'NULL'}: {row['cnt']} 个")
        
        logger.info("\n✅ transport_mode 迁移成功完成！")
        
    except Exception as e:
        logger.error(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
