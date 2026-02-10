"""移除 LOCAL 执行策略 - 将 LOCAL 更新为 AUTO_SELECT"""
from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    """将 projects 和 scheduled_tasks 表中的 LOCAL 策略更新为 AUTO_SELECT"""
    return """
        UPDATE `projects` 
        SET `execution_strategy` = 'auto'
        WHERE `execution_strategy` = 'local';
        
        UPDATE `scheduled_tasks` 
        SET `execution_strategy` = 'auto'
        WHERE `execution_strategy` = 'local';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    """不支持回滚 - LOCAL 策略已从系统中移除"""
    return ""
