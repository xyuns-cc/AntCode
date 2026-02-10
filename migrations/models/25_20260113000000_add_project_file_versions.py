"""
项目文件版本化存储迁移

新增：
1. project_file_versions 表（不可变版本）
2. project_files 表扩展字段（草稿管理、编辑状态、版本指针）
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- 1. 创建 project_file_versions 表
        CREATE TABLE IF NOT EXISTS `project_file_versions` (
            `id` BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
            `project_id` BIGINT NOT NULL,
            `version` INT NOT NULL,
            `version_id` VARCHAR(64) NOT NULL UNIQUE,
            `manifest_key` VARCHAR(512) NOT NULL,
            `artifact_key` VARCHAR(512) NOT NULL,
            `content_hash` VARCHAR(64) NOT NULL,
            `file_count` INT NOT NULL DEFAULT 0,
            `total_size` BIGINT NOT NULL DEFAULT 0,
            `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            `created_by` BIGINT,
            `description` VARCHAR(500),
            UNIQUE KEY `uk_project_version` (`project_id`, `version`),
            KEY `idx_project_id` (`project_id`),
            KEY `idx_version_id` (`version_id`),
            KEY `idx_content_hash` (`content_hash`),
            KEY `idx_created_at` (`created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

        -- 2. 扩展 project_files 表：草稿管理字段
        ALTER TABLE `project_files`
            ADD COLUMN `draft_manifest_key` VARCHAR(512) NULL COMMENT '草稿 manifest S3 路径',
            ADD COLUMN `draft_root_prefix` VARCHAR(512) NULL COMMENT '草稿文件树 S3 前缀';

        -- 3. 扩展 project_files 表：编辑状态字段
        ALTER TABLE `project_files`
            ADD COLUMN `dirty` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '草稿是否有未发布修改',
            ADD COLUMN `dirty_files_count` INT NOT NULL DEFAULT 0 COMMENT '修改文件数',
            ADD COLUMN `last_editor_id` BIGINT NULL COMMENT '最后编辑者 ID',
            ADD COLUMN `last_edit_at` DATETIME(6) NULL COMMENT '最后编辑时间';

        -- 4. 扩展 project_files 表：版本指针字段
        ALTER TABLE `project_files`
            ADD COLUMN `published_version` INT NOT NULL DEFAULT 0 COMMENT '最新已发布版本号';

        -- 5. 添加索引
        ALTER TABLE `project_files`
            ADD INDEX `idx_dirty` (`dirty`),
            ADD INDEX `idx_published_version` (`published_version`);

        -- 6. 更新 storage_type 默认值为 s3
        ALTER TABLE `project_files`
            MODIFY COLUMN `storage_type` VARCHAR(20) NOT NULL DEFAULT 's3';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- 回滚：删除新增字段和表
        ALTER TABLE `project_files`
            DROP INDEX `idx_published_version`,
            DROP INDEX `idx_dirty`,
            DROP COLUMN `published_version`,
            DROP COLUMN `last_edit_at`,
            DROP COLUMN `last_editor_id`,
            DROP COLUMN `dirty_files_count`,
            DROP COLUMN `dirty`,
            DROP COLUMN `draft_root_prefix`,
            DROP COLUMN `draft_manifest_key`;

        DROP TABLE IF EXISTS `project_file_versions`;
    """
