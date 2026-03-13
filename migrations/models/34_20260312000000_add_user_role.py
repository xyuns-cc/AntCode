"""新增用户 role 字段，支持基于角色的权限控制。"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE `users` ADD COLUMN `role` VARCHAR(20) NOT NULL DEFAULT 'user';
        UPDATE `users` SET `role` = 'admin' WHERE `is_admin` = 1;
        UPDATE `users` SET `role` = 'super_admin' WHERE `is_admin` = 1 AND `username` = 'admin';
        CREATE INDEX `idx_users_role` ON `users` (`role`);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX `idx_users_role` ON `users`;
        ALTER TABLE `users` DROP COLUMN `role`;
    """
