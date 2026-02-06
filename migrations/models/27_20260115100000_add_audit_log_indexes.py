"""
添加审计日志复合索引

优化统计查询性能，添加以下复合索引：
- (created_at, action)
- (created_at, username)
- (created_at, resource_type)
- (created_at, success)
"""

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- 添加审计日志复合索引
        CREATE INDEX `idx_audit_logs_created_action` ON `audit_logs` (`created_at`, `action`);
        CREATE INDEX `idx_audit_logs_created_username` ON `audit_logs` (`created_at`, `username`);
        CREATE INDEX `idx_audit_logs_created_resource` ON `audit_logs` (`created_at`, `resource_type`);
        CREATE INDEX `idx_audit_logs_created_success` ON `audit_logs` (`created_at`, `success`);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- 移除审计日志复合索引
        DROP INDEX `idx_audit_logs_created_action` ON `audit_logs`;
        DROP INDEX `idx_audit_logs_created_username` ON `audit_logs`;
        DROP INDEX `idx_audit_logs_created_resource` ON `audit_logs`;
        DROP INDEX `idx_audit_logs_created_success` ON `audit_logs`;
    """
