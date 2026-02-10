"""
移除外键约束

项目禁止使用数据库外键，改为应用层维护关联关系。
移除以下外键：
1. venvs.interpreter_id -> interpreters.id
2. project_venv_bindings.venv_id -> venvs.id

注意：实际外键名称可能因环境不同而异，此迁移已于 2026-01-15 手动执行。
"""

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- 移除 venvs 表的外键约束（外键名可能为 fk_venvs_interpre_0e45728b）
        -- ALTER TABLE `venvs` DROP FOREIGN KEY `fk_venvs_interpre_0e45728b`;

        -- 移除 project_venv_bindings 表的外键约束（外键名可能为 fk_project__venvs_c8c86953 或 fk_project__venvs_14a3dc05）
        -- ALTER TABLE `project_venv_bindings` DROP FOREIGN KEY `fk_project__venvs_c8c86953`;

        -- 此迁移已于 2026-01-15 手动执行，SQL 已注释
        SELECT 1;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- 恢复 venvs 表的外键约束
        ALTER TABLE `venvs`
            ADD CONSTRAINT `fk_venvs_interpre_0e45728b`
            FOREIGN KEY (`interpreter_id`) REFERENCES `interpreters` (`id`) ON DELETE RESTRICT;

        -- 恢复 project_venv_bindings 表的外键约束
        ALTER TABLE `project_venv_bindings`
            ADD CONSTRAINT `fk_project__venvs_14a3dc05`
            FOREIGN KEY (`venv_id`) REFERENCES `venvs` (`id`) ON DELETE CASCADE;
    """
