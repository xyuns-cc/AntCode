#!/bin/bash
# AntCode Worker 环境诊断脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_ROOT="$(dirname "$SCRIPT_DIR")"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS="${GREEN}OK${NC}"
FAIL="${RED}FAIL${NC}"
WARN="${YELLOW}!${NC}"

check_pass() {
    echo -e "  $PASS $1"
}

check_fail() {
    echo -e "  $FAIL $1"
}

check_warn() {
    echo -e "  $WARN $1"
}

echo "AntCode Worker 环境诊断"
echo "========================"
echo ""

# 1. Python 检查
echo "1. Python 环境"
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 --version 2>&1)
    check_pass "Python: $PY_VERSION"
else
    check_fail "Python3 未安装"
fi

# 2. uv 检查
echo ""
echo "2. uv 工具"
if command -v uv &> /dev/null; then
    UV_VERSION=$(uv --version 2>&1)
    check_pass "uv: $UV_VERSION"
else
    check_warn "uv 未安装（可选，用于虚拟环境管理）"
fi

# 3. Redis 连接检查
echo ""
echo "3. Redis 连接"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
if command -v redis-cli &> /dev/null; then
    if redis-cli -u "$REDIS_URL" ping &> /dev/null; then
        check_pass "Redis 连接正常: $REDIS_URL"
    else
        check_warn "Redis 连接失败: $REDIS_URL"
    fi
else
    check_warn "redis-cli 未安装，跳过连接检查"
fi

# 4. 目录检查
echo ""
echo "4. 目录结构"
for dir in "config" "runtime_data" "src/antcode_worker"; do
    if [ -d "$WORKER_ROOT/$dir" ]; then
        check_pass "$dir/"
    else
        check_fail "$dir/ 不存在"
    fi
done

# 5. 配置文件检查
echo ""
echo "5. 配置文件"
if [ -f "$WORKER_ROOT/config/worker.yaml" ]; then
    check_pass "config/worker.yaml"
else
    check_warn "config/worker.yaml 不存在（将使用默认配置）"
fi

# 6. 依赖检查
echo ""
echo "6. Python 依赖"
cd "$WORKER_ROOT"
if [ -f "pyproject.toml" ]; then
    check_pass "pyproject.toml 存在"
    if command -v uv &> /dev/null; then
        if uv pip list 2>/dev/null | grep -q "loguru"; then
            check_pass "核心依赖已安装"
        else
            check_warn "部分依赖可能未安装，运行 'uv sync' 安装"
        fi
    fi
else
    check_fail "pyproject.toml 不存在"
fi

# 7. 证书检查（Gateway 模式）
echo ""
echo "7. 证书（Gateway 模式）"
SECRETS_DIR="$WORKER_ROOT/runtime_data/secrets"
if [ -d "$SECRETS_DIR" ]; then
    if [ -f "$SECRETS_DIR/ca.crt" ]; then
        check_pass "CA 证书存在"
    else
        check_warn "CA 证书不存在（mTLS 需要）"
    fi
else
    check_warn "secrets 目录不存在"
fi

echo ""
echo "诊断完成"
