"""添加爬取批次表"""

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS `crawl_batches` (
    `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `public_id` VARCHAR(32) NOT NULL UNIQUE,
    `project_id` BIGINT NOT NULL COMMENT '关联项目ID',
    `name` VARCHAR(255) NOT NULL COMMENT '批次名称',
    `description` LONGTEXT COMMENT '批次描述',
    `seed_urls` JSON NOT NULL COMMENT '种子URL列表',
    `max_depth` INT NOT NULL COMMENT '最大爬取深度' DEFAULT 3,
    `max_pages` INT NOT NULL COMMENT '最大爬取页面数' DEFAULT 10000,
    `max_concurrency` INT NOT NULL COMMENT '最大并发数' DEFAULT 50,
    `request_delay` DOUBLE NOT NULL COMMENT '请求间隔(秒)' DEFAULT 0.5,
    `timeout` INT NOT NULL COMMENT '请求超时(秒)' DEFAULT 30,
    `max_retries` INT NOT NULL COMMENT '最大重试次数' DEFAULT 3,
    `status` VARCHAR(20) NOT NULL COMMENT '批次状态' DEFAULT 'pending',
    `is_test` BOOL NOT NULL COMMENT '是否为测试批次' DEFAULT 0,
    `created_at` DATETIME(6) NOT NULL COMMENT '创建时间' DEFAULT CURRENT_TIMESTAMP(6),
    `started_at` DATETIME(6) COMMENT '开始时间',
    `completed_at` DATETIME(6) COMMENT '完成时间',
    `user_id` BIGINT NOT NULL COMMENT '创建者ID',
    KEY `idx_crawl_batch_public__a1b2c3` (`public_id`),
    KEY `idx_crawl_batch_project_d4e5f6` (`project_id`),
    KEY `idx_crawl_batch_status_g7h8i9` (`status`),
    KEY `idx_crawl_batch_user_id_j0k1l2` (`user_id`),
    KEY `idx_crawl_batch_is_test_m3n4o5` (`is_test`),
    KEY `idx_crawl_batch_created_p6q7r8` (`created_at`),
    KEY `idx_crawl_batch_proj_st_s9t0u1` (`project_id`, `status`),
    KEY `idx_crawl_batch_user_st_v2w3x4` (`user_id`, `status`)
) CHARACTER SET utf8mb4 COMMENT='爬取批次';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS `crawl_batches`;"""
