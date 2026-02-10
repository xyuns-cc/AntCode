"""任务执行字段命名统一：execution_id -> run_id。

兼容 MySQL / SQLite，避免开发环境迁移失败。
"""

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = False


async def upgrade(db: BaseDBAsyncClient) -> str:
    async def using_sqlite() -> bool:
        try:
            rows = await db.execute_query_dict(
                "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
            )
            return isinstance(rows, list)
        except Exception:
            return False

    async def table_exists(table: str) -> bool:
        if await using_sqlite():
            rows = await db.execute_query_dict(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            )
            return bool(rows)

        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.tables "
            f"WHERE table_schema = DATABASE() AND table_name = '{table}'"
        )
        return bool(rows and rows[0].get("cnt"))

    async def column_exists(table: str, column: str) -> bool:
        if await using_sqlite():
            rows = await db.execute_query_dict(f"PRAGMA table_info(`{table}`)")
            return any(row.get("name") == column for row in rows)

        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.columns "
            "WHERE table_schema = DATABASE() "
            f"AND table_name = '{table}' AND column_name = '{column}'"
        )
        return bool(rows and rows[0].get("cnt"))

    async def index_exists(table: str, index_name: str) -> bool:
        if await using_sqlite():
            rows = await db.execute_query_dict(f"PRAGMA index_list(`{table}`)")
            return any(row.get("name") == index_name for row in rows)

        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() "
            f"AND table_name = '{table}' AND index_name = '{index_name}'"
        )
        return bool(rows and rows[0].get("cnt"))

    statements: list[str] = []
    is_sqlite = await using_sqlite()

    if await table_exists("task_executions"):
        if await column_exists("task_executions", "execution_id") and not await column_exists(
            "task_executions", "run_id"
        ):
            statements.append(
                "ALTER TABLE `task_executions` RENAME COLUMN `execution_id` TO `run_id`"
            )

        # 索引名统一（仅在旧索引存在且新索引不存在时执行）
        old_index = "idx_task_execut_executi_9697ff"
        new_index = "idx_task_execut_run_id_9697ff"
        if await index_exists("task_executions", old_index) and not await index_exists(
            "task_executions", new_index
        ):
            if is_sqlite:
                statements.extend(
                    [
                        f"CREATE INDEX IF NOT EXISTS `{new_index}` ON `task_executions` (`run_id`)",
                        f"DROP INDEX IF EXISTS `{old_index}`",
                    ]
                )
            else:
                statements.append(
                    f"ALTER TABLE `task_executions` RENAME INDEX `{old_index}` TO `{new_index}`"
                )

    return ";\n".join(statements) + (";" if statements else "")


async def downgrade(db: BaseDBAsyncClient) -> str:
    async def using_sqlite() -> bool:
        try:
            rows = await db.execute_query_dict(
                "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1"
            )
            return isinstance(rows, list)
        except Exception:
            return False

    async def table_exists(table: str) -> bool:
        if await using_sqlite():
            rows = await db.execute_query_dict(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'"
            )
            return bool(rows)

        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.tables "
            f"WHERE table_schema = DATABASE() AND table_name = '{table}'"
        )
        return bool(rows and rows[0].get("cnt"))

    async def column_exists(table: str, column: str) -> bool:
        if await using_sqlite():
            rows = await db.execute_query_dict(f"PRAGMA table_info(`{table}`)")
            return any(row.get("name") == column for row in rows)

        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.columns "
            "WHERE table_schema = DATABASE() "
            f"AND table_name = '{table}' AND column_name = '{column}'"
        )
        return bool(rows and rows[0].get("cnt"))

    async def index_exists(table: str, index_name: str) -> bool:
        if await using_sqlite():
            rows = await db.execute_query_dict(f"PRAGMA index_list(`{table}`)")
            return any(row.get("name") == index_name for row in rows)

        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() "
            f"AND table_name = '{table}' AND index_name = '{index_name}'"
        )
        return bool(rows and rows[0].get("cnt"))

    statements: list[str] = []
    is_sqlite = await using_sqlite()

    if await table_exists("task_executions"):
        if await column_exists("task_executions", "run_id") and not await column_exists(
            "task_executions", "execution_id"
        ):
            statements.append(
                "ALTER TABLE `task_executions` RENAME COLUMN `run_id` TO `execution_id`"
            )

        old_index = "idx_task_execut_run_id_9697ff"
        new_index = "idx_task_execut_executi_9697ff"
        if await index_exists("task_executions", old_index) and not await index_exists(
            "task_executions", new_index
        ):
            if is_sqlite:
                statements.extend(
                    [
                        f"CREATE INDEX IF NOT EXISTS `{new_index}` ON `task_executions` (`execution_id`)",
                        f"DROP INDEX IF EXISTS `{old_index}`",
                    ]
                )
            else:
                statements.append(
                    f"ALTER TABLE `task_executions` RENAME INDEX `{old_index}` TO `{new_index}`"
                )

    return ";\n".join(statements) + (";" if statements else "")
