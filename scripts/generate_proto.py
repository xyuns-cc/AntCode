#!/usr/bin/env python3
"""
Protocol Buffers code generation script.
Generates Python code from contracts/proto/ to packages/antcode_contracts.

Usage:
    python scripts/generate_proto.py
    # or
    uv run python scripts/generate_proto.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from loguru import logger


def _fix_imports(file_path: Path) -> None:
    """修复生成文件中的绝对导入为相对导入。"""
    content = file_path.read_text(encoding="utf-8")
    content = re.sub(
        r"^import ([A-Za-z0-9_]+_pb2) as ",
        r"from . import \1 as ",
        content,
        flags=re.MULTILINE,
    )
    file_path.write_text(content, encoding="utf-8")


def main() -> None:
    project_root = Path(__file__).parent.parent
    proto_dir = project_root / "contracts" / "proto"
    output_dir = project_root / "packages" / "antcode_contracts" / "src" / "antcode_contracts"

    if not proto_dir.exists():
        logger.error("未找到 proto 目录: {}", proto_dir)
        sys.exit(1)

    proto_files = sorted(proto_dir.glob("*.proto"))
    if not proto_files:
        logger.error("未找到 .proto 文件: {}", proto_dir)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={proto_dir}",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        f"--pyi_out={output_dir}",
        *[str(f) for f in proto_files],
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("生成代码失败: {}", result.stderr)
        sys.exit(1)

    # .py 与 .pyi 都要修复相对导入；此前只匹配 *.py，.pyi 里遗留的顶层
    # `import common_pb2` 让严格 mypy 直接 import-not-found。
    for generated in (*output_dir.glob("*_pb2*.py"), *output_dir.glob("*_pb2*.pyi")):
        _fix_imports(generated)

    logger.info("生成完成: {}", [f.name for f in proto_files])
    logger.info("输出目录: {}", output_dir)


if __name__ == "__main__":
    main()
