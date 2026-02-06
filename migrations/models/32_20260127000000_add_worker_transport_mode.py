"""
Migration: Add transport_mode field to workers table

Adds transport_mode column to track how worker connects (direct/gateway)
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> None:
    await db.execute_script(
        """
        ALTER TABLE workers ADD COLUMN IF NOT EXISTS transport_mode VARCHAR(20) DEFAULT 'gateway';
        
        -- Update existing workers:
        -- Direct Worker 识别条件:
        -- 1. public_id 以 'w-' 开头（本地身份管理器生成的格式）
        -- 2. host 为空或等于 'direct'
        UPDATE workers SET transport_mode = 'direct' 
        WHERE public_id LIKE 'w-%' 
           OR host = 'direct' 
           OR host = '';
        """
    )


async def downgrade(db: BaseDBAsyncClient) -> None:
    await db.execute_script(
        """
        ALTER TABLE workers DROP COLUMN IF EXISTS transport_mode;
        """
    )
