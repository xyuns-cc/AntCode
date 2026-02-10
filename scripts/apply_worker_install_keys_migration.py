#!/usr/bin/env python3
"""
直接执行 SQL 创建 worker_install_keys 表
使用 Tortoise ORM 执行原始 SQL
"""

import asyncio
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from antcode_core.infrastructure.db import get_tortoise_config
from tortoise import Tortoise


# SQL 语句
SQL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS `worker_install_keys` (
    `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `key` VARCHAR(64) NOT NULL UNIQUE,
    `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
    `os_type` VARCHAR(20) NOT NULL,
    `created_by` BIGINT NOT NULL,
    `used_by_worker` VARCHAR(32),
    `used_at` DATETIME(6),
    `expires_at` DATETIME(6) NOT NULL,
    `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) CHARACTER SET utf8mb4;
"""

SQL_CREATE_INDEXES = [
    "CREATE INDEX `idx_worker_install_keys_key` ON `worker_install_keys` (`key`);",
    "CREATE INDEX `idx_worker_install_keys_status` ON `worker_install_keys` (`status`);",
    "CREATE INDEX `idx_worker_install_keys_created_by` ON `worker_install_keys` (`created_by`);",
]


async def main():
    """执行数据库迁移"""
    print("初始化数据库连接...")
    
    try:
        # 初始化数据库连接
        config = get_tortoise_config()
        await Tortoise.init(config=config)
        
        # 获取数据库连接
        conn = Tortoise.get_connection("default")
        
        # 创建表
        print("创建 worker_install_keys 表...")
        await conn.execute_script(SQL_CREATE_TABLE)
        print("✓ 表创建成功")
        
        # 创建索引
        print("创建索引...")
        for sql in SQL_CREATE_INDEXES:
            try:
                await conn.execute_script(sql)
                print("✓ 索引创建成功")
            except Exception as e:
                if "Duplicate key name" in str(e) or "already exists" in str(e):
                    print("⚠ 索引已存在，跳过")
                else:
                    raise
        
        print("\n✅ 数据库迁移成功完成！")
        
    except Exception as e:
        print(f"\n❌ 错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
