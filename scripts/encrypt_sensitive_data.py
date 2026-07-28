"""把历史明文配置重写为当前透明加密字段格式。"""

from __future__ import annotations

import asyncio

from antcode_core.domain.models import ProjectCode, ProjectFile, ProjectRule, SystemConfig, Task
from antcode_core.infrastructure.db.tortoise import close_db, init_db


async def _rewrite(model, fields: list[str]) -> int:
    rows = await model.all()
    for row in rows:
        await row.save(update_fields=fields)
    return len(rows)


async def main() -> None:
    await init_db(service="migration")
    try:
        counts = {
            "system_configs": await _rewrite(SystemConfig, ["config_value"]),
            "project_files": await _rewrite(ProjectFile, ["runtime_config", "environment_vars"]),
            "project_codes": await _rewrite(ProjectCode, ["runtime_config", "environment_vars"]),
            "project_rules": await _rewrite(
                ProjectRule,
                ["headers", "cookies", "proxy_config", "task_config"],
            ),
            # P1-12：Task.execution_params / environment_vars 也迁移到透明加密。
            "tasks": await _rewrite(Task, ["execution_params", "environment_vars"]),
        }
        print(counts)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
