#!/usr/bin/env python3
"""
Worker 测试运行脚本

从项目根目录运行：
    uv run python scripts/run_worker_tests.py
    uv run python scripts/run_worker_tests.py tests/unit -v
    uv run python scripts/run_worker_tests.py tests/integration -v

注意：Worker 测试现在位于 services/worker/tests/ 目录下。
"""

import sys
from pathlib import Path

import pytest
from loguru import logger

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# Worker 服务目录
WORKER_SERVICE_DIR = PROJECT_ROOT / "services" / "worker"
WORKER_TESTS_DIR = WORKER_SERVICE_DIR / "tests"

# 旧的测试目录 (兼容)
LEGACY_TESTS_DIR = PROJECT_ROOT / "src" / "tasks" / "tests_worker"

if __name__ == "__main__":
    import os

    # 确定测试目录
    if WORKER_TESTS_DIR.exists():
        test_dir = WORKER_TESTS_DIR
        os.chdir(WORKER_SERVICE_DIR)
    elif LEGACY_TESTS_DIR.exists():
        test_dir = LEGACY_TESTS_DIR
        os.chdir(PROJECT_ROOT / "src" / "tasks")
    else:
        logger.error("未找到 Worker 测试目录")
        logger.error("尝试: {}", WORKER_TESTS_DIR)
        logger.error("尝试: {}", LEGACY_TESTS_DIR)
        sys.exit(1)

    # 默认运行所有测试
    args = sys.argv[1:] if len(sys.argv) > 1 else [str(test_dir)]

    logger.info("运行测试目录: {}", test_dir)
    logger.info("工作目录: {}", os.getcwd())
    logger.info("参数: {}", args)

    # 运行 pytest
    sys.exit(pytest.main(args))
