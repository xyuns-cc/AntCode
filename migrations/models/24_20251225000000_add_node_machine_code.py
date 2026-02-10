"""添加 nodes 表的 machine_code 字段"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE `nodes` ADD COLUMN `machine_code` VARCHAR(32) UNIQUE;
        CREATE INDEX `idx_nodes_machine_code` ON `nodes` (`machine_code`);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX `idx_nodes_machine_code` ON `nodes`;
        ALTER TABLE `nodes` DROP COLUMN `machine_code`;
    """
