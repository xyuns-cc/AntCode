"""统一 runtime/worker 命名口径并清理遗留表。"""

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

    async def common_columns(source: str, target: str) -> list[str]:
        rows = await db.execute_query_dict(
            "SELECT s.column_name AS col "
            "FROM information_schema.columns AS s "
            "INNER JOIN information_schema.columns AS t "
            "  ON s.column_name = t.column_name "
            "WHERE s.table_schema = DATABASE() "
            "  AND t.table_schema = DATABASE() "
            "  AND s.table_name = %s "
            "  AND t.table_name = %s "
            "ORDER BY s.ordinal_position",
            [source, target],
        )
        return [row["col"] for row in rows]

    statements: list[str] = []

    async def merge_legacy_table(source: str, target: str) -> None:
        source_exists = await table_exists(source)
        if not source_exists:
            return

        target_exists = await table_exists(target)
        if not target_exists:
            statements.append(f"RENAME TABLE `{source}` TO `{target}`")
            return

        columns = await common_columns(source, target)
        if columns:
            cols = ", ".join(f"`{column}`" for column in columns)
            has_public_id = await column_exists(source, "public_id") and await column_exists(
                target, "public_id"
            )
            if has_public_id:
                statements.append(
                    f"INSERT IGNORE INTO `{target}` ({cols}) "
                    f"SELECT {cols} FROM `{source}` AS legacy "
                    f"WHERE NOT EXISTS ("
                    f"SELECT 1 FROM `{target}` AS current "
                    f"WHERE current.`public_id` = legacy.`public_id`"
                    f")"
                )
            else:
                statements.append(
                    f"INSERT IGNORE INTO `{target}` ({cols}) SELECT {cols} FROM `{source}`"
                )

        statements.append(f"DROP TABLE `{source}`")

    async def rename_index_any(candidates: tuple[str, ...], old: str, new: str) -> None:
        for table in candidates:
            if not await table_exists(table):
                continue
            if await index_exists(table, old) and not await index_exists(table, new):
                statements.append(f"ALTER TABLE `{table}` RENAME INDEX `{old}` TO `{new}`")
                return
    worker_legacy_pairs = [
        ("nodes", "workers"),
        ("node_projects", "worker_projects"),
        ("node_project_files", "worker_project_files"),
        ("node_heartbeats", "worker_heartbeats"),
        ("node_events", "worker_events"),
        ("node_performance_history", "worker_performance_history"),
        ("user_node_permissions", "user_worker_permissions"),
    ]
    for source, target in worker_legacy_pairs:
        await merge_legacy_table(source, target)


    index_renames = [
        (("projects",), "idx_projects_node_id_abfbb6", "idx_projects_worker_id_abfbb6"),
        (("projects",), "idx_projects_venv_sc_328cdd", "idx_projects_runtime_scope_328cdd"),
        (("projects",), "idx_projects_venv_node_id", "idx_projects_runtime_worker_id"),
        (
            ("scheduled_tasks",),
            "idx_scheduled_t_node_id_61ae00",
            "idx_scheduled_t_worker_id_61ae00",
        ),
        (
            ("scheduled_tasks",),
            "idx_scheduled_t_node_id_2b5fff",
            "idx_scheduled_t_worker_id_2b5fff",
        ),
        (
            ("scheduled_tasks",),
            "idx_scheduled_t_node_id_1b1f58",
            "idx_scheduled_t_worker_id_1b1f58",
        ),
        (
            ("spider_metrics_history",),
            "idx_spider_metr_node_id_96b6d5",
            "idx_spider_metr_worker_id_96b6d5",
        ),
        (
            ("spider_metrics_history",),
            "idx_spider_metr_node_id_e4f459",
            "idx_spider_metr_worker_id_e4f459",
        ),
        (("task_executions",), "execution_id", "run_id"),
        (
            ("task_executions",),
            "idx_task_execut_node_id_1f5043",
            "idx_task_execut_worker_id_1f5043",
        ),
        (("venvs", "runtimes"), "idx_venvs_public__d1fb03", "idx_runtimes_public__d1fb03"),
        (("venvs", "runtimes"), "idx_venvs_scope_9c7596", "idx_runtimes_scope_9c7596"),
        (("venvs", "runtimes"), "idx_venvs_version_be2e9b", "idx_runtimes_version_be2e9b"),
        (("venvs", "runtimes"), "idx_venvs_created_109e7e", "idx_runtimes_created_109e7e"),
        (("venvs", "runtimes"), "idx_venvs_created_ab0203", "idx_runtimes_created_ab0203"),
        (("venvs", "runtimes"), "idx_venvs_scope_fecab2", "idx_runtimes_scope_fecab2"),
        (
            ("venvs", "runtimes"),
            "idx_venvs_node_id_12560f",
            "idx_runtimes_worker_id_12560f",
        ),
        (
            ("venvs", "runtimes"),
            "idx_venvs_node_id_f265bb",
            "idx_runtimes_worker_id_f265bb",
        ),
        (
            ("venvs", "runtimes"),
            "fk_venvs_interpre_0e45728b",
            "fk_runtimes_interpre_0e45728b",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_ven_public__6860a9",
            "idx_project_runtime_public__6860a9",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_ven_project_b10b50",
            "idx_project_runtime_project_b10b50",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_ven_venv_id_a505d3",
            "idx_project_runtime_runtime_id_a505d3",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_ven_created_05a81f",
            "idx_project_runtime_created_05a81f",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_ven_created_c7a542",
            "idx_project_runtime_created_c7a542",
        ),
    ]

    for candidates, old, new in index_renames:
        await rename_index_any(candidates, old, new)

    runtime_legacy_pairs = [
        ("venvs", "runtimes"),
        ("project_venv_bindings", "project_runtime_bindings"),
    ]
    for source, target in runtime_legacy_pairs:
        await merge_legacy_table(source, target)

    return ";\n".join(statements) + (";" if statements else "")


async def downgrade(db: BaseDBAsyncClient) -> str:
    async def table_exists(table: str) -> bool:
        rows = await db.execute_query_dict(
            "SELECT COUNT(*) AS cnt FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            [table],
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

    async def rename_index_any(candidates: tuple[str, ...], old: str, new: str) -> None:
        for table in candidates:
            if not await table_exists(table):
                continue
            if await index_exists(table, old) and not await index_exists(table, new):
                statements.append(f"ALTER TABLE `{table}` RENAME INDEX `{old}` TO `{new}`")
                return

    reverse_index_renames = [
        (("projects",), "idx_projects_worker_id_abfbb6", "idx_projects_node_id_abfbb6"),
        (("projects",), "idx_projects_runtime_scope_328cdd", "idx_projects_venv_sc_328cdd"),
        (("projects",), "idx_projects_runtime_worker_id", "idx_projects_venv_node_id"),
        (
            ("scheduled_tasks",),
            "idx_scheduled_t_worker_id_61ae00",
            "idx_scheduled_t_node_id_61ae00",
        ),
        (
            ("scheduled_tasks",),
            "idx_scheduled_t_worker_id_2b5fff",
            "idx_scheduled_t_node_id_2b5fff",
        ),
        (
            ("scheduled_tasks",),
            "idx_scheduled_t_worker_id_1b1f58",
            "idx_scheduled_t_node_id_1b1f58",
        ),
        (
            ("spider_metrics_history",),
            "idx_spider_metr_worker_id_96b6d5",
            "idx_spider_metr_node_id_96b6d5",
        ),
        (
            ("spider_metrics_history",),
            "idx_spider_metr_worker_id_e4f459",
            "idx_spider_metr_node_id_e4f459",
        ),
        (("task_executions",), "run_id", "execution_id"),
        (
            ("task_executions",),
            "idx_task_execut_worker_id_1f5043",
            "idx_task_execut_node_id_1f5043",
        ),
        (("venvs", "runtimes"), "idx_runtimes_public__d1fb03", "idx_venvs_public__d1fb03"),
        (("venvs", "runtimes"), "idx_runtimes_scope_9c7596", "idx_venvs_scope_9c7596"),
        (("venvs", "runtimes"), "idx_runtimes_version_be2e9b", "idx_venvs_version_be2e9b"),
        (("venvs", "runtimes"), "idx_runtimes_created_109e7e", "idx_venvs_created_109e7e"),
        (("venvs", "runtimes"), "idx_runtimes_created_ab0203", "idx_venvs_created_ab0203"),
        (("venvs", "runtimes"), "idx_runtimes_scope_fecab2", "idx_venvs_scope_fecab2"),
        (
            ("venvs", "runtimes"),
            "idx_runtimes_worker_id_12560f",
            "idx_venvs_node_id_12560f",
        ),
        (
            ("venvs", "runtimes"),
            "idx_runtimes_worker_id_f265bb",
            "idx_venvs_node_id_f265bb",
        ),
        (
            ("venvs", "runtimes"),
            "fk_runtimes_interpre_0e45728b",
            "fk_venvs_interpre_0e45728b",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_runtime_public__6860a9",
            "idx_project_ven_public__6860a9",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_runtime_project_b10b50",
            "idx_project_ven_project_b10b50",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_runtime_runtime_id_a505d3",
            "idx_project_ven_venv_id_a505d3",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_runtime_created_05a81f",
            "idx_project_ven_created_05a81f",
        ),
        (
            ("project_venv_bindings", "project_runtime_bindings"),
            "idx_project_runtime_created_c7a542",
            "idx_project_ven_created_c7a542",
        ),
    ]

    for candidates, old, new in reverse_index_renames:
        await rename_index_any(candidates, old, new)

    if await table_exists("project_runtime_bindings") and not await table_exists(
        "project_venv_bindings"
    ):
        statements.append("RENAME TABLE `project_runtime_bindings` TO `project_venv_bindings`")

    if await table_exists("runtimes") and not await table_exists("venvs"):
        statements.append("RENAME TABLE `runtimes` TO `venvs`")

    return ";\n".join(statements) + (";" if statements else "")
