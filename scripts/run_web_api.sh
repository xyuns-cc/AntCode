#!/bin/bash
# =============================================================================
# Web API 服务启动脚本
# 启动 Control Plane (FastAPI Web 服务)
# =============================================================================

set -e

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 默认配置
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
RELOAD="${RELOAD:-false}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
UVICORN_LOG_LEVEL="$(printf '%s' "$LOG_LEVEL" | tr '[:upper:]' '[:lower:]')"
SCHEDULER_ROLE="${SCHEDULER_ROLE:-control}"

# 帮助信息
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "启动 AntCode Web API 服务 (Control Plane)"
    echo ""
    echo "Options:"
    echo "  --host HOST       绑定地址 (默认: 0.0.0.0)"
    echo "  --port PORT       监听端口 (默认: 8000)"
    echo "  --workers N       Worker 进程数 (默认: 1)"
    echo "  --reload          启用热重载 (开发模式)"
    echo "  --log-level LEVEL 日志级别 (默认: info)"
    echo "  -h, --help        显示帮助信息"
    echo ""
    echo "环境变量:"
    echo "  HOST              绑定地址"
    echo "  PORT              监听端口"
    echo "  WORKERS           Worker 进程数"
    echo "  RELOAD            是否启用热重载 (true/false)"
    echo "  LOG_LEVEL         日志级别"
    echo "  SCHEDULER_ROLE     调度器角色 (默认: control)"
    echo ""
    echo "示例:"
    echo "  $0                           # 使用默认配置启动"
    echo "  $0 --port 8080 --reload      # 开发模式，端口 8080"
    echo "  $0 --workers 4               # 生产模式，4 个 worker"
}

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            HOST="$2"
            shift 2
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --reload)
            RELOAD="true"
            shift
            ;;
        --log-level)
            LOG_LEVEL="$2"
            shift 2
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "未知选项: $1"
            show_help
            exit 1
            ;;
    esac
done

echo "=== AntCode Web API 服务 ==="
echo "项目根目录: $PROJECT_ROOT"
echo "绑定地址: $HOST:$PORT"
echo "Worker 数: $WORKERS"
echo "热重载: $RELOAD"
echo "日志级别: $LOG_LEVEL (uvicorn: $UVICORN_LOG_LEVEL)"
echo "调度器角色: $SCHEDULER_ROLE"
echo ""

cd "$PROJECT_ROOT"

export LOG_LEVEL
export SCHEDULER_ROLE

# 构建 uvicorn 命令
UVICORN_ARGS=(
    "antcode_web_api:app"
    "--host" "$HOST"
    "--port" "$PORT"
    "--log-level" "$UVICORN_LOG_LEVEL"
)

if [ "$RELOAD" = "true" ]; then
    UVICORN_ARGS+=("--reload")
else
    UVICORN_ARGS+=("--workers" "$WORKERS")
fi

echo "启动命令: uv run uvicorn ${UVICORN_ARGS[*]}"
echo ""

# 启动服务
exec uv run uvicorn "${UVICORN_ARGS[@]}"
