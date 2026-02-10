"""迁移 env_location 字段值"""

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE `projects`
        SET `env_location` = 'node'
        WHERE `env_location` = 'local';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE `projects`
        SET `env_location` = 'local'
        WHERE `env_location` = 'node';
    """
