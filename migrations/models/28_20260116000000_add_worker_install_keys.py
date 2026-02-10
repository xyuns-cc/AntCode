"""添加 worker_install_keys 表"""

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
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
        CREATE INDEX `idx_worker_install_keys_key` ON `worker_install_keys` (`key`);
        CREATE INDEX `idx_worker_install_keys_status` ON `worker_install_keys` (`status`);
        CREATE INDEX `idx_worker_install_keys_created_by` ON `worker_install_keys` (`created_by`);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS `worker_install_keys`;
    """
