#!/bin/bash
# =============================================================================
# Worker 执行器服务启动脚本
# 启动 Execution Plane (任务执行器)
# =============================================================================

set -e

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 默认配置
WORKER_NAME="${WORKER_NAME:-}"
WORKER_PORT="${WORKER_PORT:-8001}"
WORKER_TRANSPORT_MODE="${WORKER_TRANSPORT_MODE:-gateway}"
WORKER_GATEWAY_HOST="${WORKER_GATEWAY_HOST:-localhost}"
WORKER_GATEWAY_PORT="${WORKER_GATEWAY_PORT:-50051}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LOG_LEVEL="$(printf '%s' "$LOG_LEVEL" | tr '[:lower:]' '[:upper:]')"
WORKER_MAX_CONCURRENT_TASKS="${WORKER_MAX_CONCURRENT_TASKS:-5}"
WORKER_HEARTBEAT_INTERVAL="${WORKER_HEARTBEAT_INTERVAL:-10}"

# 帮助信息
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "启动 AntCode Worker 执行器服务 (Execution Plane)"
    echo ""
    echo "Options:"
    echo "  --name NAME               Worker 名称 (默认: 自动生成)"
    echo "  --port PORT               Worker 端口 (默认: 8001)"
    echo "  --mode MODE               传输模式: direct|gateway (默认: gateway)"
    echo "  --gateway-host HOST       Gateway 地址 (gateway 模式)"
    echo "  --gateway-port PORT       Gateway 端口 (gateway 模式, 默认: 50051)"
    echo "  --log-level LEVEL         日志级别 (默认: INFO)"
    echo "  --max-concurrent N        最大并发任务数 (默认: 5)"
    echo "  --heartbeat-interval SEC  心跳间隔 (默认: 10)"
    echo "  -h, --help                显示帮助信息"
    echo ""
    echo "环境变量:"
    echo "  WORKER_NAME               Worker 名称"
    echo "  WORKER_PORT               Worker 端口"
    echo "  WORKER_TRANSPORT_MODE     传输模式"
    echo "  WORKER_GATEWAY_HOST       Gateway 地址"
    echo "  WORKER_GATEWAY_PORT       Gateway 端口"
    echo "  LOG_LEVEL                 日志级别"
    echo "  WORKER_MAX_CONCURRENT_TASKS 最大并发任务数"
    echo "  WORKER_HEARTBEAT_INTERVAL 心跳间隔"
    echo "  ANTCODE_WORKER_KEY        Worker 安装 Key"
    echo ""
    echo "传输模式说明:"
    echo "  direct   - 内网直连 Redis Streams"
    echo "  gateway  - 公网通过 Gateway gRPC 连接"
    echo ""
    echo "示例:"
    echo "  $0                                    # 使用默认配置启动 (gateway 模式)"
    echo "  $0 --name Worker-001 --port 8001     # 指定名称和端口"
    echo "  $0 --mode gateway --gateway-host gw.example.com  # Gateway 模式"
}

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)
            WORKER_NAME="$2"
            shift 2
            ;;
        --port)
            WORKER_PORT="$2"
            shift 2
            ;;
        --mode)
            WORKER_TRANSPORT_MODE="$2"
            shift 2
            ;;
        --gateway-host)
            WORKER_GATEWAY_HOST="$2"
            shift 2
            ;;
        --gateway-port)
            WORKER_GATEWAY_PORT="$2"
            shift 2
            ;;
        --log-level)
            LOG_LEVEL="$2"
            shift 2
            ;;
        --max-concurrent)
            WORKER_MAX_CONCURRENT_TASKS="$2"
            shift 2
            ;;
        --heartbeat-interval)
            WORKER_HEARTBEAT_INTERVAL="$2"
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

cd "$PROJECT_ROOT"

echo "=== AntCode Worker 执行器服务 ==="
echo "项目根目录: $PROJECT_ROOT"
echo "Worker 名称: ${WORKER_NAME:-<自动生成>}"
echo "Worker 端口: $WORKER_PORT"
echo "传输模式: $WORKER_TRANSPORT_MODE"
if [ "$WORKER_TRANSPORT_MODE" = "gateway" ]; then
    echo "Gateway 地址: $WORKER_GATEWAY_HOST:$WORKER_GATEWAY_PORT"
fi
echo "日志级别: $LOG_LEVEL"
echo "最大并发: $WORKER_MAX_CONCURRENT_TASKS"
echo "心跳间隔: ${WORKER_HEARTBEAT_INTERVAL}s"
echo ""

# 设置环境变量
export WORKER_PORT
export WORKER_TRANSPORT_MODE
export WORKER_GATEWAY_HOST
export WORKER_GATEWAY_PORT
export LOG_LEVEL
export WORKER_MAX_CONCURRENT_TASKS
export WORKER_HEARTBEAT_INTERVAL

# 构建命令参数
WORKER_ARGS=("run" "--port" "$WORKER_PORT" "--transport" "$WORKER_TRANSPORT_MODE" "--log-level" "$LOG_LEVEL")

if [ -n "$WORKER_NAME" ]; then
    WORKER_ARGS+=("--name" "$WORKER_NAME")
fi

if [ "$WORKER_TRANSPORT_MODE" = "gateway" ]; then
    WORKER_ARGS+=("--gateway-host" "$WORKER_GATEWAY_HOST" "--gateway-port" "$WORKER_GATEWAY_PORT")
fi

echo "启动命令: uv run python -m antcode_worker ${WORKER_ARGS[*]}"
echo ""

# 启动服务
exec uv run python -m antcode_worker "${WORKER_ARGS[@]}"
