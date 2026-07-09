#!/usr/bin/env python3
"""
Worker 节点启动脚本

从项目根目录运行：
    uv run python scripts/run_worker.py
    uv run python scripts/run_worker.py --name Worker-001 --port 8001
    uv run python scripts/run_worker.py --show-machine-code

注意：推荐使用 scripts/run_worker.sh 启动脚本，提供更多配置选项。
"""

from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

try:
    from antcode_worker.cli import main
except ImportError as exc:
    raise SystemExit(
        "无法导入 antcode_worker，请先在项目根目录执行 `uv sync` 安装依赖。"
    ) from exc

if __name__ == "__main__":
    main()
