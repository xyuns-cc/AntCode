#!/bin/bash
# =============================================================================
# Proto 代码生成脚本
# 从 contracts/proto/ 生成 Python 代码到 packages/antcode_contracts/
# =============================================================================

set -e

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 目录配置
PROTO_DIR="$PROJECT_ROOT/contracts/proto"
OUTPUT_DIR="$PROJECT_ROOT/packages/antcode_contracts/src/antcode_contracts"

echo "=== AntCode Proto 代码生成 ==="
echo "Proto 目录: $PROTO_DIR"
echo "输出目录: $OUTPUT_DIR"
echo ""

# 检查 proto 目录是否存在
if [ ! -d "$PROTO_DIR" ]; then
    echo "错误: Proto 目录不存在: $PROTO_DIR"
    exit 1
fi

# 检查是否有 proto 文件
PROTO_FILES=$(find "$PROTO_DIR" -name "*.proto" -type f)
if [ -z "$PROTO_FILES" ]; then
    echo "错误: 未找到 .proto 文件"
    exit 1
fi

echo "找到 proto 文件:"
for f in $PROTO_FILES; do
    echo "  - $(basename "$f")"
done
echo ""

# 创建输出目录
mkdir -p "$OUTPUT_DIR"

# 生成 Python 代码
echo "正在生成 Python 代码..."
uv run python -m grpc_tools.protoc \
    --proto_path="$PROTO_DIR" \
    --python_out="$OUTPUT_DIR" \
    --grpc_python_out="$OUTPUT_DIR" \
    --pyi_out="$OUTPUT_DIR" \
    $PROTO_FILES

echo "生成完成，正在修复导入..."

# 修复生成文件中的导入语句
fix_imports() {
    local file="$1"
    if [ -f "$file" ]; then
        # macOS 和 Linux 兼容的 sed 命令
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS
            sed -i '' 's/^import common_pb2 as/from . import common_pb2 as/g' "$file"
            sed -i '' 's/^import gateway_pb2 as/from . import gateway_pb2 as/g' "$file"
        else
            # Linux
            sed -i 's/^import common_pb2 as/from . import common_pb2 as/g' "$file"
            sed -i 's/^import gateway_pb2 as/from . import gateway_pb2 as/g' "$file"
        fi
    fi
}

# 修复所有生成的 Python 文件
for py_file in "$OUTPUT_DIR"/*_pb2*.py; do
    if [ -f "$py_file" ]; then
        fix_imports "$py_file"
        echo "  修复: $(basename "$py_file")"
    fi
done

echo ""
echo "=== 生成完成 ==="
echo "生成的文件:"
ls -la "$OUTPUT_DIR"/*.py 2>/dev/null || echo "  (无 .py 文件)"
echo ""
echo "验证导入..."

# 验证生成的代码可以导入
cd "$PROJECT_ROOT"
uv run python -c "
import logging
import sys
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)
sys.path.insert(0, '$OUTPUT_DIR/..')
try:
    from antcode_contracts import common_pb2
    logger.info('  OK  common_pb2 导入成功')
except ImportError as e:
    logger.error('  FAIL common_pb2 导入失败: %s', e)
    sys.exit(1)

try:
    from antcode_contracts import gateway_pb2
    logger.info('  OK  gateway_pb2 导入成功')
except ImportError as e:
    logger.error('  FAIL gateway_pb2 导入失败: %s', e)
    sys.exit(1)

logger.info('所有模块导入验证通过')
"

echo ""
echo "=== 完成 ==="
