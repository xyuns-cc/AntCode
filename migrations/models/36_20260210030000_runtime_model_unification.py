"""统一 runtime 抽象字段（runtime_kind/runtime_locator/runtime_details）。"""

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

    async def column_nullable(table: str, column: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
            [table, column],
        )
        if not rows:
            return False
        row = rows[0]
        value = row.get("is_nullable", row.get("IS_NULLABLE", "NO"))
        return value == "YES"

    async def index_exists(table: str, index_name: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s",
            [table, index_name],
        )
        return bool(rows and rows[0]["cnt"])

    statements: list[str] = []

    if await table_exists("runtimes"):
        if await column_exists("runtimes", "venv_path") and not await column_exists(
            "runtimes", "runtime_locator"
        ):
            statements.append(
                "ALTER TABLE `runtimes` CHANGE COLUMN `venv_path` `runtime_locator` VARCHAR(500) NOT NULL"
            )

        if not await column_exists("runtimes", "runtime_kind"):
            statements.append(
                "ALTER TABLE `runtimes` ADD COLUMN `runtime_kind` VARCHAR(20) NOT NULL DEFAULT 'python' AFTER `public_id`"
            )

        if not await column_exists("runtimes", "runtime_details"):
            statements.append(
                "ALTER TABLE `runtimes` ADD COLUMN `runtime_details` JSON NULL AFTER `runtime_locator`"
            )

        if await column_exists("runtimes", "runtime_details"):
            statements.append(
                "UPDATE `runtimes` "
                "SET `runtime_details` = JSON_OBJECT('python_version', `version`) "
                "WHERE `runtime_details` IS NULL"
            )

        if await column_exists("runtimes", "interpreter_id") and not await column_nullable(
            "runtimes", "interpreter_id"
        ):
            statements.append("ALTER TABLE `runtimes` MODIFY COLUMN `interpreter_id` BIGINT NULL")

        if not await index_exists("runtimes", "idx_runtimes_runtime_kind"):
            statements.append(
                "CREATE INDEX `idx_runtimes_runtime_kind` ON `runtimes` (`runtime_kind`)"
            )

        if not await index_exists("runtimes", "idx_runtimes_kind_scope_version"):
            statements.append(
                "CREATE INDEX `idx_runtimes_kind_scope_version` ON `runtimes` (`runtime_kind`, `scope`, `version`)"
            )

    if await table_exists("projects"):
        if await column_exists("projects", "venv_path") and not await column_exists(
            "projects", "runtime_locator"
        ):
            statements.append(
                "ALTER TABLE `projects` CHANGE COLUMN `venv_path` `runtime_locator` VARCHAR(500) NULL"
            )

        if not await column_exists("projects", "runtime_kind"):
            statements.append(
                "ALTER TABLE `projects` ADD COLUMN `runtime_kind` VARCHAR(20) NULL AFTER `runtime_scope`"
            )

        if await column_exists("projects", "runtime_kind"):
            statements.append(
                "UPDATE `projects` SET `runtime_kind` = 'python' "
                "WHERE `runtime_kind` IS NULL "
                "AND (`python_version` IS NOT NULL OR `runtime_scope` IS NOT NULL OR `runtime_locator` IS NOT NULL)"
            )

        if not await index_exists("projects", "idx_projects_runtime_kind"):
            statements.append(
                "CREATE INDEX `idx_projects_runtime_kind` ON `projects` (`runtime_kind`)"
            )

        if not await index_exists("projects", "idx_projects_runtime_locator"):
            statements.append(
                "CREATE INDEX `idx_projects_runtime_locator` ON `projects` (`runtime_locator`)"
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

    if await table_exists("projects"):
        if await index_exists("projects", "idx_projects_runtime_locator"):
            statements.append("DROP INDEX `idx_projects_runtime_locator` ON `projects`")

        if await index_exists("projects", "idx_projects_runtime_kind"):
            statements.append("DROP INDEX `idx_projects_runtime_kind` ON `projects`")

        if await column_exists("projects", "runtime_kind"):
            statements.append("ALTER TABLE `projects` DROP COLUMN `runtime_kind`")

        if await column_exists("projects", "runtime_locator") and not await column_exists(
            "projects", "venv_path"
        ):
            statements.append(
                "ALTER TABLE `projects` CHANGE COLUMN `runtime_locator` `venv_path` VARCHAR(500) NULL"
            )

    if await table_exists("runtimes"):
        if await index_exists("runtimes", "idx_runtimes_kind_scope_version"):
            statements.append("DROP INDEX `idx_runtimes_kind_scope_version` ON `runtimes`")

        if await index_exists("runtimes", "idx_runtimes_runtime_kind"):
            statements.append("DROP INDEX `idx_runtimes_runtime_kind` ON `runtimes`")

        if await column_exists("runtimes", "runtime_details"):
            statements.append("ALTER TABLE `runtimes` DROP COLUMN `runtime_details`")

        if await column_exists("runtimes", "runtime_kind"):
            statements.append("ALTER TABLE `runtimes` DROP COLUMN `runtime_kind`")

        if await column_exists("runtimes", "runtime_locator") and not await column_exists(
            "runtimes", "venv_path"
        ):
            statements.append(
                "ALTER TABLE `runtimes` CHANGE COLUMN `runtime_locator` `venv_path` VARCHAR(500) NOT NULL"
            )

    return ";\n".join(statements) + (";" if statements else "")
