"""统一 user_sessions 表中的 node 命名为 worker。"""

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = False


async def upgrade(db: BaseDBAsyncClient) -> str:
    async def table_exists(table: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            [table],
        )
        return bool(rows and rows[0]["cnt"])

    async def column_exists(table: str, column: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
            [table, column],
        )
        return bool(rows and rows[0]["cnt"])

    async def index_exists(table: str, index_name: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
            [table, index_name],
        )
        return bool(rows and rows[0]["cnt"])

    statements: list[str] = []

    if await table_exists("user_sessions"):
        if await column_exists("user_sessions", "node_id") and not await column_exists(
            "user_sessions", "worker_id"
        ):
            statements.append("ALTER TABLE `user_sessions` RENAME COLUMN `node_id` TO `worker_id`")

        if await index_exists("user_sessions", "idx_user_sessions_node_id") and not await index_exists(
            "user_sessions", "idx_user_sessions_worker_id"
        ):
            statements.append(
                "ALTER TABLE `user_sessions` RENAME INDEX `idx_user_sessions_node_id` TO `idx_user_sessions_worker_id`"
            )

        if await index_exists(
            "user_sessions", "idx_user_sessions_node_session_type"
        ) and not await index_exists("user_sessions", "idx_user_sessions_worker_session_type"):
            statements.append(
                "ALTER TABLE `user_sessions` RENAME INDEX `idx_user_sessions_node_session_type` TO `idx_user_sessions_worker_session_type`"
            )

    return ";\n".join(statements) + (";" if statements else "")


async def downgrade(db: BaseDBAsyncClient) -> str:
    async def table_exists(table: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            [table],
        )
        return bool(rows and rows[0]["cnt"])

    async def column_exists(table: str, column: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
            [table, column],
        )
        return bool(rows and rows[0]["cnt"])

    async def index_exists(table: str, index_name: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
            [table, index_name],
        )
        return bool(rows and rows[0]["cnt"])

    statements: list[str] = []

    if await table_exists("user_sessions"):
        if await index_exists(
            "user_sessions", "idx_user_sessions_worker_session_type"
        ) and not await index_exists("user_sessions", "idx_user_sessions_node_session_type"):
            statements.append(
                "ALTER TABLE `user_sessions` RENAME INDEX `idx_user_sessions_worker_session_type` TO `idx_user_sessions_node_session_type`"
            )

        if await index_exists("user_sessions", "idx_user_sessions_worker_id") and not await index_exists(
            "user_sessions", "idx_user_sessions_node_id"
        ):
            statements.append(
                "ALTER TABLE `user_sessions` RENAME INDEX `idx_user_sessions_worker_id` TO `idx_user_sessions_node_id`"
            )

        if await column_exists("user_sessions", "worker_id") and not await column_exists(
            "user_sessions", "node_id"
        ):
            statements.append("ALTER TABLE `user_sessions` RENAME COLUMN `worker_id` TO `node_id`")

    return ";\n".join(statements) + (";" if statements else "")
