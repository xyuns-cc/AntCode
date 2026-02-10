#!/usr/bin/env python3
"""
批量迁移 src.* 导入到 antcode_core 的脚本
"""

import os
import re
from pathlib import Path

from loguru import logger
# 导入映射规则
IMPORT_MAPPINGS = {
    # src.models -> antcode_core.domain.models
    "from src.models.": "from antcode_core.domain.models.",
    "from src.models import": "from antcode_core.domain.models import",
    "import src.models.": "import antcode_core.domain.models.",
    
    # src.schemas -> antcode_core.domain.schemas
    "from src.schemas.": "from antcode_core.domain.schemas.",
    "from src.schemas import": "from antcode_core.domain.schemas import",
    "import src.schemas.": "import antcode_core.domain.schemas.",
    
    # src.core.config -> antcode_core.common.config
    "from src.core.config import": "from antcode_core.common.config import",
    "from src.core.config ": "from antcode_core.common.config ",
    
    # src.core.logging -> antcode_core.common.logging
    "from src.core.logging import": "from antcode_core.common.logging import",
    
    # src.core.exceptions -> antcode_core.common.exceptions
    "from src.core.exceptions import": "from antcode_core.common.exceptions import",
    
    # src.core.response -> antcode_core.common.response
    "from src.core.response import": "from antcode_core.common.response import",
    
    # src.core.security -> antcode_core.common.security
    "from src.core.security.": "from antcode_core.common.security.",
    "from src.core.security import": "from antcode_core.common.security import",
    
    # src.core.db_config -> antcode_core.infrastructure.db.tortoise
    "from src.core.db_config import": "from antcode_core.infrastructure.db.tortoise import",
    
    # src.common -> antcode_core.common
    "from src.common.serialization import": "from antcode_core.common.serialization import",
    "from src.common.hash_utils import": "from antcode_core.common.hash_utils import",
    "from src.common.exceptions import": "from antcode_core.common.exceptions import",
    
    # src.infrastructure.redis -> antcode_core.infrastructure.redis
    "from src.infrastructure.redis import": "from antcode_core.infrastructure.redis import",
    "from src.infrastructure.redis.": "from antcode_core.infrastructure.redis.",
    
    # src.infrastructure.cache -> antcode_core.infrastructure.cache
    "from src.infrastructure.cache import": "from antcode_core.infrastructure.cache import",
    "from src.infrastructure.cache.": "from antcode_core.infrastructure.cache.",
    
    # src.services.users -> antcode_core.domain.services.users
    "from src.services.users.user_service import": "from antcode_core.domain.services.users import",
    "from src.services.users import": "from antcode_core.domain.services.users import",
    
    # src.services.audit -> antcode_core.domain.services.audit
    "from src.services.audit import": "from antcode_core.domain.services.audit import",
    "from src.services.audit.": "from antcode_core.domain.services.audit.",
    
    # src.services.alert -> antcode_core.domain.services.alert
    "from src.services.alert import": "from antcode_core.domain.services.alert import",
    "from src.services.alert.": "from antcode_core.domain.services.alert.",
    
    # src.services.projects -> antcode_core.domain.services.projects
    "from src.services.projects.": "from antcode_core.domain.services.projects.",
    "from src.services.projects import": "from antcode_core.domain.services.projects import",
    
    # src.services.scheduler -> antcode_core.domain.services.scheduler
    "from src.services.scheduler.": "from antcode_core.domain.services.scheduler.",
    "from src.services.scheduler import": "from antcode_core.domain.services.scheduler import",
    
    # src.services.nodes -> antcode_core.domain.services.nodes
    "from src.services.nodes.": "from antcode_core.domain.services.nodes.",
    "from src.services.nodes import": "from antcode_core.domain.services.nodes import",
    
    # src.services.logs -> antcode_core.domain.services.logs
    "from src.services.logs.": "from antcode_core.domain.services.logs.",
    "from src.services.logs import": "from antcode_core.domain.services.logs import",
    
    
    # src.services.files -> antcode_core.domain.services.files
    "from src.services.files.": "from antcode_core.domain.services.files.",
    "from src.services.files import": "from antcode_core.domain.services.files import",
    
    # src.services.monitoring -> antcode_core.domain.services.monitoring
    "from src.services.monitoring import": "from antcode_core.domain.services.monitoring import",
    "from src.services.monitoring.": "from antcode_core.domain.services.monitoring.",
    
    # src.services.system_config -> antcode_core.domain.services.system_config
    "from src.services.system_config import": "from antcode_core.domain.services.system_config import",
    "from src.services.system_config.": "from antcode_core.domain.services.system_config.",
    
    # src.services.crawl -> antcode_core.domain.services.crawl
    "from src.services.crawl.": "from antcode_core.domain.services.crawl.",
    "from src.services.crawl import": "from antcode_core.domain.services.crawl import",
    
    
    # src.services.base -> antcode_core.domain.services.base
    "from src.services.base import": "from antcode_core.domain.services.base import",
    
    # src.utils -> antcode_core.common.utils
    "from src.utils.": "from antcode_core.common.utils.",
    "from src.utils import": "from antcode_core.common.utils import",
    
    # src.core.resilience -> antcode_core.infrastructure.resilience
    "from src.core.resilience.": "from antcode_core.infrastructure.resilience.",
    "from src.core.resilience import": "from antcode_core.infrastructure.resilience import",
}


def migrate_file(filepath: Path, dry_run: bool = False) -> tuple[bool, list[str]]:
    """
    迁移单个文件的导入
    
    Returns:
        (changed, changes): 是否有变更，变更列表
    """
    try:
        content = filepath.read_text(encoding='utf-8')
    except Exception as e:
        logger.warning("无法读取文件 {}: {}", filepath, e)
        return False, []
    
    original_content = content
    changes = []
    
    for old_import, new_import in IMPORT_MAPPINGS.items():
        if old_import in content:
            content = content.replace(old_import, new_import)
            changes.append(f"  {old_import} -> {new_import}")
    
    if content != original_content:
        if not dry_run:
            filepath.write_text(content, encoding='utf-8')
        return True, changes
    
    return False, []


def migrate_directory(directory: Path, dry_run: bool = False) -> dict:
    """迁移目录下所有 Python 文件"""
    stats = {
        "total_files": 0,
        "changed_files": 0,
        "changes": []
    }
    
    for filepath in directory.rglob("*.py"):
        if "__pycache__" in str(filepath):
            continue
        
        stats["total_files"] += 1
        changed, changes = migrate_file(filepath, dry_run)
        
        if changed:
            stats["changed_files"] += 1
            stats["changes"].append({
                "file": str(filepath),
                "changes": changes
            })
            logger.info("{}修改: {}", "[DRY-RUN] " if dry_run else "", filepath)
            for change in changes:
                logger.info("{}", change)
    
    return stats


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="迁移 src.* 导入到 antcode_core")
    parser.add_argument("--dry-run", action="store_true", help="只显示变更，不实际修改")
    parser.add_argument("--dir", type=str, default=".", help="要迁移的目录")
    args = parser.parse_args()
    
    directory = Path(args.dir)
    
    logger.info("开始迁移 {} 下的导入...", directory)
    logger.info("模式: {}", "DRY-RUN (不实际修改)" if args.dry_run else "实际修改")
    logger.info("-" * 60)
    
    stats = migrate_directory(directory, args.dry_run)
    
    logger.info("-" * 60)
    logger.info("总文件数: {}", stats["total_files"])
    logger.info("修改文件数: {}", stats["changed_files"])


if __name__ == "__main__":
    main()
