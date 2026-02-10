"""添加 venv_node_id 字段到 Project 模型"""
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    """添加 venv_node_id 字段和索引"""
    return """
        ALTER TABLE `projects` ADD COLUMN `venv_node_id` BIGINT NULL;
        CREATE INDEX `idx_projects_venv_node_id` ON `projects` (`venv_node_id`);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    """移除 venv_node_id 字段和索引"""
    return """
        DROP INDEX IF EXISTS `idx_projects_venv_node_id` ON `projects`;
        ALTER TABLE `projects` DROP COLUMN `venv_node_id`;
    """
