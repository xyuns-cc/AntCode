#!/bin/bash
# =============================================================================
# Proto 代码生成脚本（薄封装）
# 权威实现是 scripts/generate_proto.py：它会同时修复 .py 和 .pyi 的相对导入。
# 本脚本不再自带 protoc/sed 逻辑，避免两个入口行为漂移。
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"
exec uv run python scripts/generate_proto.py "$@"
