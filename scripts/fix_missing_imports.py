#!/usr/bin/env python3
"""
批量修复 web_api 中缺失的导入
"""

import re
from pathlib import Path

from loguru import logger
project_root = Path(__file__).parent.parent
web_api_dir = project_root / "services" / "web_api" / "src" / "antcode_web_api"


def fix_missing_imports(file_path: Path):
    """修复缺失的导入"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # 修复 serialization 导入 - 暂时保持使用 src.common
    content = re.sub(
        r'from antcode_core\.common\.serialization import',
        'from src.common.serialization import',
        content
    )

    # 修复 response 导入 - 暂时保持使用 src.core
    content = re.sub(
        r'from antcode_core\.common\.response import',
        'from src.core.response import',
        content
    )

    # 修复 resilience 导入
    content = re.sub(
        r'from antcode_core\.infrastructure\.resilience',
        'from src.core.resilience',
        content
    )

    # 修复 cache 导入
    content = re.sub(
        r'from antcode_core\.infrastructure\.cache',
        'from src.infrastructure.cache',
        content
    )

    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False


logger.info("=" * 60)
logger.info("批量修复缺失的导入")
logger.info("=" * 60)

fixed_count = 0

for py_file in web_api_dir.rglob("*.py"):
    if fix_missing_imports(py_file):
        logger.info("修复: {}", py_file.relative_to(project_root))
        fixed_count += 1

logger.info("修复完成，共修复 {} 个文件", fixed_count)
logger.info("说明：")
logger.info("- serialization, response, resilience, cache 等模块暂时保持使用 src.*")
logger.info("- 这些模块将在后续任务中迁移到 antcode_core")
