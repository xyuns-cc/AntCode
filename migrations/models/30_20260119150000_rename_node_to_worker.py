"""节点命名统一为 worker（表名与字段名重命名）"""

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

    statements: list[str] = []

    async def rename_table(old: str, new: str) -> None:
        if await table_exists(old) and not await table_exists(new):
            statements.append(f"RENAME TABLE `{old}` TO `{new}`")

    async def rename_column(table: str, old: str, new: str) -> None:
        if await table_exists(table) and await column_exists(table, old) and not await column_exists(table, new):
            statements.append(f"ALTER TABLE `{table}` RENAME COLUMN `{old}` TO `{new}`")

    await rename_table("nodes", "workers")
    await rename_table("node_heartbeats", "worker_heartbeats")
    await rename_table("node_projects", "worker_projects")
    await rename_table("node_project_files", "worker_project_files")
    await rename_table("node_events", "worker_events")
    await rename_table("node_performance_history", "worker_performance_history")
    await rename_table("user_node_permissions", "user_worker_permissions")

    await rename_column("scheduled_tasks", "specified_node_id", "specified_worker_id")
    await rename_column("scheduled_tasks", "node_id", "worker_id")

    await rename_column("task_executions", "node_id", "worker_id")

    await rename_column("projects", "node_id", "worker_id")
    await rename_column("projects", "node_env_name", "worker_env_name")
    await rename_column("projects", "venv_scope", "runtime_scope")
    await rename_column("projects", "current_venv_id", "current_runtime_id")
    await rename_column("projects", "venv_node_id", "runtime_worker_id")
    await rename_column("projects", "bound_node_id", "bound_worker_id")

    await rename_column("venvs", "node_id", "worker_id")

    await rename_column("project_venv_bindings", "venv_id", "runtime_id")

    await rename_column("spider_metrics_history", "node_id", "worker_id")

    await rename_column("worker_projects", "node_id", "worker_id")
    await rename_column("worker_projects", "node_local_project_id", "worker_local_project_id")

    await rename_column("worker_project_files", "node_project_id", "worker_project_id")

    await rename_column("worker_heartbeats", "node_id", "worker_id")
    await rename_column("worker_events", "node_id", "worker_id")
    await rename_column("worker_performance_history", "node_id", "worker_id")

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

    statements: list[str] = []

    async def rename_table(old: str, new: str) -> None:
        if await table_exists(old) and not await table_exists(new):
            statements.append(f"RENAME TABLE `{old}` TO `{new}`")

    async def rename_column(table: str, old: str, new: str) -> None:
        if await table_exists(table) and await column_exists(table, old) and not await column_exists(table, new):
            statements.append(f"ALTER TABLE `{table}` RENAME COLUMN `{old}` TO `{new}`")

    await rename_column("worker_performance_history", "worker_id", "node_id")
    await rename_column("worker_events", "worker_id", "node_id")
    await rename_column("worker_heartbeats", "worker_id", "node_id")
    await rename_column("worker_project_files", "worker_project_id", "node_project_id")
    await rename_column("worker_projects", "worker_id", "node_id")
    await rename_column("worker_projects", "worker_local_project_id", "node_local_project_id")
    await rename_column("spider_metrics_history", "worker_id", "node_id")
    await rename_column("project_venv_bindings", "runtime_id", "venv_id")
    await rename_column("venvs", "worker_id", "node_id")
    await rename_column("projects", "worker_id", "node_id")
    await rename_column("projects", "worker_env_name", "node_env_name")
    await rename_column("projects", "runtime_scope", "venv_scope")
    await rename_column("projects", "current_runtime_id", "current_venv_id")
    await rename_column("projects", "runtime_worker_id", "venv_node_id")
    await rename_column("projects", "bound_worker_id", "bound_node_id")
    await rename_column("task_executions", "worker_id", "node_id")
    await rename_column("scheduled_tasks", "specified_worker_id", "specified_node_id")
    await rename_column("scheduled_tasks", "worker_id", "node_id")

    await rename_table("user_worker_permissions", "user_node_permissions")
    await rename_table("worker_performance_history", "node_performance_history")
    await rename_table("worker_events", "node_events")
    await rename_table("worker_project_files", "node_project_files")
    await rename_table("worker_projects", "node_projects")
    await rename_table("worker_heartbeats", "node_heartbeats")
    await rename_table("workers", "nodes")

    return ";\n".join(statements) + (";" if statements else "")


MODELS_STATE = (
    "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"
)
