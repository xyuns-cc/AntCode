#!/usr/bin/env python3
"""
数据库迁移管理脚本

提供便捷的数据库迁移命令封装。

使用方法:
    # 初始化迁移（首次使用）
    uv run python scripts/db_migrate.py init
    
    # 生成迁移文件
    uv run python scripts/db_migrate.py migrate --name "add_user_table"
    
    # 应用迁移
    uv run python scripts/db_migrate.py upgrade
    
    # 回滚迁移
    uv run python scripts/db_migrate.py downgrade
    
    # 查看迁移历史
    uv run python scripts/db_migrate.py history
    
    # 查看当前版本
    uv run python scripts/db_migrate.py heads
"""
import argparse
import asyncio
import sys
import shutil
from pathlib import Path

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.dont_write_bytecode = True


def _cleanup_pycache() -> None:
    for pycache_dir in (PROJECT_ROOT / "migrations").rglob("__pycache__"):
        shutil.rmtree(pycache_dir, ignore_errors=True)


async def run_aerich_command(args: list[str]) -> int:
    """运行 aerich 命令"""
    from aerich import Command
    from src.core.db_config import TORTOISE_ORM
    from tortoise import Tortoise

    _cleanup_pycache()

    command = Command(
        tortoise_config=TORTOISE_ORM,
        app="models",
        location="./migrations",
    )

    try:
        await command.init()

        if args[0] == "init":
            # 初始化迁移目录
            migrations_dir = PROJECT_ROOT / "migrations" / "models"
            has_migrations = migrations_dir.exists() and any(
                p.is_file() and p.suffix == ".py" and p.name[0].isdigit()
                for p in migrations_dir.iterdir()
            )
            if has_migrations:
                print("ℹ️  已存在迁移文件，跳过 init-db，请直接执行 upgrade")
            else:
                await command.init_db(safe=True)
                print("✅ 迁移初始化完成")

        elif args[0] == "migrate":
            # 生成迁移文件
            name = None
            if "--name" in args:
                idx = args.index("--name")
                if idx + 1 < len(args):
                    name = args[idx + 1]
            result = await command.migrate(name=name or "update")
            if result:
                print(f"✅ 迁移文件已生成: {result}")
            else:
                print("ℹ️  没有检测到模型变更")

        elif args[0] == "upgrade":
            # 应用迁移
            await command.upgrade(run_in_transaction=True)
            print("✅ 迁移已应用")

        elif args[0] == "downgrade":
            # 回滚迁移
            version = -1
            delete = False
            if "--version" in args:
                idx = args.index("--version")
                if idx + 1 < len(args):
                    version = int(args[idx + 1])
            if "--delete" in args:
                delete = True
            await command.downgrade(version=version, delete=delete)
            print("✅ 迁移已回滚")

        elif args[0] == "history":
            # 查看迁移历史
            versions = await command.history()
            if versions:
                print("📋 迁移历史:")
                for v in versions:
                    print(f"  - {v}")
            else:
                print("ℹ️  暂无迁移历史")

        elif args[0] == "heads":
            # 查看当前版本
            heads = await command.heads()
            if heads:
                print(f"📌 当前版本: {heads}")
            else:
                print("ℹ️  暂无迁移版本")

        else:
            print(f"❌ 未知命令: {args[0]}")
            return 1

        return 0

    except Exception as e:
        print(f"❌ 错误: {e}")
        return 1
    finally:
        try:
            await Tortoise.close_connections()
        except Exception:
            pass
        _cleanup_pycache()


def main():
    parser = argparse.ArgumentParser(
        description="AntCode 数据库迁移管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
命令说明:
  init                    初始化迁移目录（首次使用）
  migrate [--name NAME]   生成迁移文件
  upgrade                 应用所有待执行的迁移
  downgrade [--version N] 回滚迁移（默认回滚 1 个版本）
  history                 查看迁移历史
  heads                   查看当前数据库版本

示例:
  uv run python scripts/db_migrate.py init
  uv run python scripts/db_migrate.py migrate --name "add_audit_log"
  uv run python scripts/db_migrate.py upgrade
  uv run python scripts/db_migrate.py downgrade --version 1
        """,
    )
    parser.add_argument(
        "command",
        choices=["init", "migrate", "upgrade", "downgrade", "history", "heads"],
        help="迁移命令",
    )
    parser.add_argument("--name", help="迁移名称（用于 migrate 命令）")
    parser.add_argument("--version", type=int, help="目标版本（用于 downgrade 命令）")
    parser.add_argument("--delete", action="store_true", help="删除迁移文件（用于 downgrade 命令）")

    args = parser.parse_args()

    # 构建命令参数
    cmd_args = [args.command]
    if args.name:
        cmd_args.extend(["--name", args.name])
    if args.version is not None:
        cmd_args.extend(["--version", str(args.version)])
    if args.delete:
        cmd_args.append("--delete")

    # 运行命令
    exit_code = asyncio.run(run_aerich_command(cmd_args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
