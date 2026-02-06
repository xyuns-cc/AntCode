#!/bin/bash
# AntCode Worker 启动脚本
# 支持 Package Mode 和 Source Mode

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_ROOT="$(dirname "$SCRIPT_DIR")"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检测运行模式
detect_mode() {
    # 检查是否作为包安装
    if command -v antcode-worker &> /dev/null; then
        echo "package"
    elif [ -f "$WORKER_ROOT/src/antcode_worker/__main__.py" ]; then
        echo "source"
    else
        echo "unknown"
    fi
}

# Package Mode 运行
run_package_mode() {
    log_info "使用 Package Mode 运行"
    antcode-worker "$@"
}

# Source Mode 运行
run_source_mode() {
    log_info "使用 Source Mode 运行"
    cd "$WORKER_ROOT"
    
    # 检查 uv
    if command -v uv &> /dev/null; then
        uv run python -m antcode_worker "$@"
    else
        # 检查 vendor 目录
        if [ -d "$WORKER_ROOT/vendor" ]; then
            export PYTHONPATH="$WORKER_ROOT/vendor:$WORKER_ROOT/src:$PYTHONPATH"
        else
            export PYTHONPATH="$WORKER_ROOT/src:$PYTHONPATH"
        fi
        python -m antcode_worker "$@"
    fi
}

# 打印配置
print_config() {
    log_info "当前配置:"
    echo "  WORKER_ROOT: $WORKER_ROOT"
    echo "  MODE: $(detect_mode)"
    echo "  PYTHONPATH: ${PYTHONPATH:-<not set>}"
}

# 主函数
main() {
    case "${1:-}" in
        --print-config)
            print_config
            exit 0
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --print-config    打印当前配置"
            echo "  --help, -h        显示帮助"
            echo ""
            echo "其他参数将传递给 antcode_worker"
            exit 0
            ;;
    esac
    
    MODE=$(detect_mode)
    
    case "$MODE" in
        package)
            run_package_mode "$@"
            ;;
        source)
            run_source_mode "$@"
            ;;
        *)
            log_error "无法检测运行模式"
            exit 1
            ;;
    esac
}

main "$@"
