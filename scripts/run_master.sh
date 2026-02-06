#!/bin/bash
# =============================================================================
# Master 调度服务启动脚本
# 启动 Schedule Plane (调度循环 + 一致性维护)
# =============================================================================

set -e

# 获取脚本所在目录的父目录（项目根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 默认配置
LOG_LEVEL="${LOG_LEVEL:-INFO}"
LOG_LEVEL="$(printf '%s' "$LOG_LEVEL" | tr '[:lower:]' '[:upper:]')"
LEADER_TTL="${LEADER_TTL:-30}"
SCHEDULER_INTERVAL="${SCHEDULER_INTERVAL:-5}"
DISPATCHER_INTERVAL="${DISPATCHER_INTERVAL:-1}"
RETRY_INTERVAL="${RETRY_INTERVAL:-10}"
RECONCILE_INTERVAL="${RECONCILE_INTERVAL:-60}"
SCHEDULER_ROLE="${SCHEDULER_ROLE:-master}"

# 帮助信息
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "启动 AntCode Master 调度服务 (Schedule Plane)"
    echo ""
    echo "Options:"
    echo "  --log-level LEVEL         日志级别 (默认: INFO)"
    echo "  --leader-ttl SECONDS      Leader 锁 TTL (默认: 30)"
    echo "  --scheduler-interval SEC  调度循环间隔 (默认: 5)"
    echo "  --dispatcher-interval SEC 分发循环间隔 (默认: 1)"
    echo "  --retry-interval SEC      重试循环间隔 (默认: 10)"
    echo "  --reconcile-interval SEC  补偿循环间隔 (默认: 60)"
    echo "  -h, --help                显示帮助信息"
    echo ""
    echo "环境变量:"
    echo "  LOG_LEVEL                 日志级别"
    echo "  LEADER_TTL                Leader 锁 TTL"
    echo "  SCHEDULER_INTERVAL        调度循环间隔"
    echo "  DISPATCHER_INTERVAL       分发循环间隔"
    echo "  RETRY_INTERVAL            重试循环间隔"
    echo "  RECONCILE_INTERVAL        补偿循环间隔"
    echo "  SCHEDULER_ROLE            调度器角色 (默认: master)"
    echo ""
    echo "示例:"
    echo "  $0                                    # 使用默认配置启动"
    echo "  $0 --log-level debug                  # 调试模式"
    echo "  $0 --scheduler-interval 10            # 调整调度间隔"
}

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --log-level)
            LOG_LEVEL="$2"
            shift 2
            ;;
        --leader-ttl)
            LEADER_TTL="$2"
            shift 2
            ;;
        --scheduler-interval)
            SCHEDULER_INTERVAL="$2"
            shift 2
            ;;
        --dispatcher-interval)
            DISPATCHER_INTERVAL="$2"
            shift 2
            ;;
        --retry-interval)
            RETRY_INTERVAL="$2"
            shift 2
            ;;
        --reconcile-interval)
            RECONCILE_INTERVAL="$2"
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

echo "=== AntCode Master 调度服务 ==="
echo "项目根目录: $PROJECT_ROOT"
echo "日志级别: $LOG_LEVEL"
echo "Leader TTL: ${LEADER_TTL}s"
echo "调度间隔: ${SCHEDULER_INTERVAL}s"
echo "分发间隔: ${DISPATCHER_INTERVAL}s"
echo "重试间隔: ${RETRY_INTERVAL}s"
echo "补偿间隔: ${RECONCILE_INTERVAL}s"
echo "调度器角色: ${SCHEDULER_ROLE}"
echo ""

cd "$PROJECT_ROOT"

# 设置环境变量
export LOG_LEVEL
export LEADER_TTL
export SCHEDULER_INTERVAL
export DISPATCHER_INTERVAL
export RETRY_INTERVAL
export RECONCILE_INTERVAL
export SCHEDULER_ROLE

echo "启动命令: uv run python -m antcode_master"
echo ""

# 启动服务
exec uv run python -m antcode_master
