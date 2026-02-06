#!/bin/bash
# =============================================================================
# Gateway 网关服务启动脚本
# 启动 Data Plane (公网 Worker 接入)
# =============================================================================

set -e

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 默认配置
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-50051}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LOG_LEVEL="$(printf '%s' "$LOG_LEVEL" | tr '[:lower:]' '[:upper:]')"
MAX_WORKERS="${MAX_WORKERS:-10}"
TLS_ENABLED="${TLS_ENABLED:-false}"
TLS_CERT="${TLS_CERT:-}"
TLS_KEY="${TLS_KEY:-}"
TLS_CA="${TLS_CA:-}"

# 帮助信息
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "启动 AntCode Gateway 网关服务 (Data Plane)"
    echo ""
    echo "Options:"
    echo "  --host HOST           绑定地址 (默认: 0.0.0.0)"
    echo "  --port PORT           gRPC 端口 (默认: 50051)"
    echo "  --log-level LEVEL     日志级别 (默认: INFO)"
    echo "  --max-workers N       最大并发 Worker 数 (默认: 10)"
    echo "  --tls                 启用 TLS"
    echo "  --tls-cert FILE       TLS 证书文件"
    echo "  --tls-key FILE        TLS 私钥文件"
    echo "  --tls-ca FILE         TLS CA 证书 (用于 mTLS)"
    echo "  -h, --help            显示帮助信息"
    echo ""
    echo "环境变量:"
    echo "  GRPC_HOST             绑定地址"
    echo "  GRPC_PORT             gRPC 端口"
    echo "  GRPC_MAX_WORKERS      最大并发 Worker 数"
    echo "  GRPC_TLS_CERT_PATH    TLS 证书文件路径"
    echo "  GRPC_TLS_KEY_PATH     TLS 私钥文件路径"
    echo "  GRPC_TLS_CA_PATH      TLS CA 证书路径"
    echo "  LOG_LEVEL             日志级别"
    echo ""
    echo "示例:"
    echo "  $0                                    # 使用默认配置启动"
    echo "  $0 --port 50051                       # 指定端口"
    echo "  $0 --tls --tls-cert cert.pem --tls-key key.pem  # 启用 TLS"
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
        --log-level)
            LOG_LEVEL="$2"
            shift 2
            ;;
        --max-workers)
            MAX_WORKERS="$2"
            shift 2
            ;;
        --tls)
            TLS_ENABLED="true"
            shift
            ;;
        --tls-cert)
            TLS_CERT="$2"
            shift 2
            ;;
        --tls-key)
            TLS_KEY="$2"
            shift 2
            ;;
        --tls-ca)
            TLS_CA="$2"
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

echo "=== AntCode Gateway 网关服务 ==="
echo "项目根目录: $PROJECT_ROOT"
echo "绑定地址: $HOST:$PORT"
echo "日志级别: $LOG_LEVEL"
echo "最大 Worker 数: $MAX_WORKERS"
echo "TLS 启用: $TLS_ENABLED"
if [ "$TLS_ENABLED" = "true" ]; then
    echo "TLS 证书: $TLS_CERT"
    echo "TLS 私钥: $TLS_KEY"
    [ -n "$TLS_CA" ] && echo "TLS CA: $TLS_CA (mTLS)"
fi
echo ""

cd "$PROJECT_ROOT"

# 设置环境变量
export GRPC_HOST="$HOST"
export GRPC_PORT="$PORT"
export GRPC_MAX_WORKERS="$MAX_WORKERS"
export GRPC_TLS_CERT_PATH="$TLS_CERT"
export GRPC_TLS_KEY_PATH="$TLS_KEY"
export GRPC_TLS_CA_PATH="$TLS_CA"
export LOG_LEVEL

echo "启动命令: uv run python -m antcode_gateway"
echo ""

# 启动服务
exec uv run python -m antcode_gateway
