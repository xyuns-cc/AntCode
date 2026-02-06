#!/usr/bin/env python3
"""
修复 web_api 中的导入路径
"""

import os
import re
from pathlib import Path

from loguru import logger
project_root = Path(__file__).parent.parent
web_api_dir = project_root / "services" / "web_api" / "src" / "antcode_web_api"


def fix_imports_in_file(file_path: Path):
    """修复单个文件中的导入"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # 修复 websocket 内部导入
    content = re.sub(
        r'from src\.services\.websockets\.websocket_connection_manager import',
        'from antcode_web_api.websockets.websocket_connection_manager import',
        content
    )
    content = re.sub(
        r'from src\.services\.websockets\.websocket_log_service import',
        'from antcode_web_api.websockets.websocket_log_service import',
        content
    )

    # 修复 common 导入
    content = re.sub(
        r'from src\.common\.serialization import',
        'from antcode_core.common.serialization import',
        content
    )

    # 修复 utils 导入
    content = re.sub(
        r'from src\.utils\.http_client import',
        'from antcode_core.infrastructure.http_client import',
        content
    )

    # 如果内容有变化，写回文件
    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info("修复: {}", file_path.relative_to(project_root))
        return True
    return False


logger.info("=" * 60)
logger.info("修复 web_api 导入路径")
logger.info("=" * 60)

fixed_count = 0

# 遍历所有 Python 文件
for py_file in web_api_dir.rglob("*.py"):
    if fix_imports_in_file(py_file):
        fixed_count += 1

logger.info("修复完成，共修复 {} 个文件", fixed_count)
logger.info("注意：src.services 的导入保持不变，这些服务将在后续任务中迁移")
