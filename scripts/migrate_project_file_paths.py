"""Migrate project file paths to storage-relative paths."""

import asyncio
import os
import sys

from loguru import logger
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from tortoise import Tortoise  # noqa: E402
from tortoise.transactions import in_transaction  # noqa: E402

from antcode_core.common.config import settings  # noqa: E402
from antcode_core.domain.models import ProjectFile  # noqa: E402


def _has_parent_ref(path):
    parts = path.replace("\\", "/").split("/")
    return any(part == ".." for part in parts)


def _validate_relative(path, label):
    if path.startswith("~"):
        raise ValueError(f"{label} does not allow ~ paths: {path}")
    if os.path.isabs(path):
        raise ValueError(f"{label} does not allow absolute paths: {path}")
    if _has_parent_ref(path):
        raise ValueError(f"{label} does not allow parent directory references: {path}")


def _convert_path(path, storage_root, label):
    if not path:
        return path, False

    if os.path.isabs(path):
        normalized = os.path.normpath(path)
        root = os.path.normpath(storage_root)

        if os.path.commonpath([normalized, root]) != root:
            raise ValueError(f"{label} path is outside storage root: {path}")

        rel = os.path.relpath(normalized, root)
        if os.path.isabs(rel) or rel.startswith("..") or _has_parent_ref(rel):
            raise ValueError(f"{label} invalid relative path: {path} -> {rel}")
        return rel, True

    _validate_relative(path, label)
    return path, False


def _convert_additional_files(additional_files, storage_root, label_prefix):
    if additional_files is None:
        return additional_files, 0, False
    if not isinstance(additional_files, list):
        raise ValueError(f"{label_prefix} is not a list")

    changed = False
    changed_count = 0

    for idx, item in enumerate(additional_files):
        if not isinstance(item, dict):
            raise ValueError(f"{label_prefix}[{idx}] is not a dict")

        for key in ("file_path", "original_file_path"):
            if key in item and item.get(key):
                new_value, updated = _convert_path(
                    item.get(key),
                    storage_root,
                    f"{label_prefix}[{idx}].{key}",
                )
                if updated:
                    item[key] = new_value
                    changed = True
                    changed_count += 1

    return additional_files, changed_count, changed


async def _verify_no_absolute_paths(storage_root):
    issues = []
    async for project_file in ProjectFile.all():
        for key in ("file_path", "original_file_path"):
            value = getattr(project_file, key)
            if value and os.path.isabs(value):
                issues.append((project_file.id, key, value))

        additional_files = project_file.additional_files or []
        if isinstance(additional_files, list):
            for item in additional_files:
                if not isinstance(item, dict):
                    continue
                for key in ("file_path", "original_file_path"):
                    value = item.get(key)
                    if value and os.path.isabs(value):
                        issues.append((project_file.id, f"additional_files.{key}", value))

    if issues:
        details = "\n".join(
            f"- project_file_id={item_id} field={field} value={value}"
            for item_id, field, value in issues
        )
        raise ValueError(f"Absolute paths still exist, fix them and retry:\n{details}")


async def migrate():
    await Tortoise.init(config=settings.TORTOISE_ORM)
    storage_root = settings.LOCAL_STORAGE_PATH

    total = 0
    updated = 0
    updated_paths = 0
    updated_additional = 0

    async with in_transaction() as conn:
        async for project_file in ProjectFile.all().using_db(conn):
            total += 1
            update_fields = []

            file_path, file_path_changed = _convert_path(
                project_file.file_path,
                storage_root,
                f"project_files[{project_file.id}].file_path",
            )
            if file_path_changed:
                project_file.file_path = file_path
                update_fields.append("file_path")
                updated_paths += 1

            original_path, original_changed = _convert_path(
                project_file.original_file_path,
                storage_root,
                f"project_files[{project_file.id}].original_file_path",
            )
            if original_changed:
                project_file.original_file_path = original_path
                update_fields.append("original_file_path")
                updated_paths += 1

            additional_files, changed_count, additional_changed = _convert_additional_files(
                project_file.additional_files,
                storage_root,
                f"project_files[{project_file.id}].additional_files",
            )
            if additional_changed:
                project_file.additional_files = additional_files
                update_fields.append("additional_files")
                updated_additional += changed_count

            if update_fields:
                updated += 1
                await project_file.save(using_db=conn, update_fields=update_fields)

    await _verify_no_absolute_paths(storage_root)
    await Tortoise.close_connections()

    logger.info("迁移完成")
    logger.info("总记录数: {}", total)
    logger.info("更新记录数: {}", updated)
    logger.info("更新路径字段: {}", updated_paths)
    logger.info("更新附加文件路径: {}", updated_additional)


if __name__ == "__main__":
    try:
        asyncio.run(migrate())
    except Exception as exc:
        logger.error("迁移失败: {}", exc)
        sys.exit(1)
