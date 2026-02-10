#!/usr/bin/env python3
"""
移除 LOCAL 执行策略迁移脚本

将 projects 和 scheduled_tasks 表中的 LOCAL 策略更新为 AUTO_SELECT。

用法:
    uv run python scripts/migrate_local_strategy.py
    uv run python scripts/migrate_local_strategy.py --dry-run
"""
import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger  # noqa: E402
from tortoise import Tortoise  # noqa: E402
from tortoise.transactions import in_transaction  # noqa: E402

from antcode_core.infrastructure.db.tortoise import TORTOISE_ORM  # noqa: E402


async def count_local_strategy_records() -> tuple[int, int]:
    """统计使用 LOCAL 策略的记录数（使用原始 SQL，因为 LOCAL 已从枚举中移除）"""
    from tortoise import connections

    conn = connections.get("default")

    result = await conn.execute_query(
        "SELECT COUNT(*) as cnt FROM projects WHERE execution_strategy = 'local'"
    )
    project_count = result[1][0]["cnt"] if result[1] else 0

    result = await conn.execute_query(
        "SELECT COUNT(*) as cnt FROM scheduled_tasks WHERE execution_strategy = 'local'"
    )
    task_count = result[1][0]["cnt"] if result[1] else 0

    return project_count, task_count


async def migrate_local_to_auto_select(dry_run: bool = False) -> tuple[int, int]:
    """将 LOCAL 策略迁移到 AUTO_SELECT，在事务中执行确保原子性"""
    project_count, task_count = await count_local_strategy_records()

    logger.info(f"待迁移: {project_count} 个项目, {task_count} 个任务")

    if dry_run:
        return project_count, task_count

    if project_count == 0 and task_count == 0:
        logger.info("无需迁移")
        return 0, 0

    updated_projects = 0
    updated_tasks = 0

    try:
        async with in_transaction() as conn:
            if project_count > 0:
                result = await conn.execute_query(
                    "UPDATE projects SET execution_strategy = 'auto' WHERE execution_strategy = 'local'"
                )
                updated_projects = result[0]

            if task_count > 0:
                result = await conn.execute_query(
                    "UPDATE scheduled_tasks SET execution_strategy = 'auto' WHERE execution_strategy = 'local'"
                )
                updated_tasks = result[0]

        # 验证
        remaining = await count_local_strategy_records()
        if remaining[0] > 0 or remaining[1] > 0:
            logger.warning(f"残留记录: {remaining[0]} 项目, {remaining[1]} 任务")

        return updated_projects, updated_tasks

    except Exception as e:
        logger.error(f"迁移失败，已回滚: {e}")
        raise


async def run_migration(dry_run: bool = False) -> int:
    """运行迁移"""
    try:
        await Tortoise.init(config=TORTOISE_ORM)

        updated_projects, updated_tasks = await migrate_local_to_auto_select(dry_run)

        mode = "预览" if dry_run else "完成"
        logger.info(f"迁移{mode}: {updated_projects} 项目, {updated_tasks} 任务")
        return 0

    except Exception as e:
        logger.error(f"迁移失败: {e}")
        return 1

    finally:
        await Tortoise.close_connections()


def main():
    parser = argparse.ArgumentParser(description="将 LOCAL 执行策略迁移到 AUTO_SELECT")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, format="{time:HH:mm:ss} | {level: <8} | {message}", level="INFO")

    sys.exit(asyncio.run(run_migration(dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
