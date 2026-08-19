#!/bin/sh
# Docker healthcheck 包装器：让 `retries` 与 `start_period` 真正生效。
#
# 背景 —— 为什么 `test:` 里不能直接写 `kill -TERM 1`：
#   Compose（非 Swarm）**从不**根据 health 状态做任何动作，`restart:` 只对 PID 1
#   退出生效。所以本仓历史上把自愈写成 `<probe> || { kill -TERM 1; exit 1; }`。
#   kill 发生在 test 命令**内部**，于是：
#     * `retries: N` 永远攒不到 N 次连续失败——第一次失败就已经杀了 PID 1；
#     * `start_period` 同样失效——它只让失败"不计入 unhealthy"，命令照跑照杀，
#       启动慢于一个 interval 的容器会被打进重启循环。
#   运维看到的 3/5/6 次容错余量，实际一次都没有。
#
# 本脚本把"探测"和"重启决策"分开：探测结果照常决定容器的 health 状态（供
# `depends_on: service_healthy` 与 `up --wait` 使用），只有连续失败达到
# max_failures **且**已过 grace_seconds 才 `kill -TERM 1` 交给 restart policy。
#
# 用法:
#   antcode-healthcheck <max_failures> <grace_seconds> <probe>
#     max_failures  —— 必须与该服务 healthcheck 的 `retries` 相同
#     grace_seconds —— 必须与该服务 healthcheck 的 `start_period` 相同（秒）
#     probe         —— 交给 `sh -c` 执行的探测命令；退出码 0 视为通过
#
# 语义细节（有意为之，不是妥协）：
#   * 连续失败计数存在 STATE_DIR（默认 /tmp/antcode-healthcheck，测试用
#     ANTCODE_HEALTHCHECK_STATE_DIR 指到临时目录）。该目录必须落在 tmpfs 上，容器
#     每次启动都是空的，计数与 grace 起点因此天然按"本次容器生命周期"计算
#     （tests/unit/core/test_docker_compose_healthcheck_contract.py 锁住这个前提）。
#   * grace 起点记在**第一次探测**时，即容器启动后约一个 interval，因此实际宽限
#     略长于 start_period。宁可多等一个 interval，也不要把启动中的容器打死。
#   * 计数在探测**之前**落盘：探测被 Docker 的 `timeout` 杀掉（探针挂死）时，
#     这一次仍然计入连续失败，否则最该重启的"卡死"场景永远攒不到次数。
#     卡死场景的重启发生在下一次探测开始时（比快速失败晚一个 interval）。
set -u

STATE_DIR=${ANTCODE_HEALTHCHECK_STATE_DIR:-/tmp/antcode-healthcheck}
FAILURES_FILE="$STATE_DIR/consecutive-failures"
GRACE_ANCHOR_FILE="$STATE_DIR/first-probe-at"
EXIT_USAGE=2
REQUIRED_ARGC=3

if [ "$#" -ne "$REQUIRED_ARGC" ]; then
    echo "antcode-healthcheck: 用法 <max_failures> <grace_seconds> <probe>" >&2
    exit "$EXIT_USAGE"
fi

max_failures=$1
grace_seconds=$2
probe=$3

mkdir -p "$STATE_DIR"
now=$(date -u +%s)
[ -f "$GRACE_ANCHOR_FILE" ] || echo "$now" >"$GRACE_ANCHOR_FILE"

grace_elapsed() {
    [ "$((now - $(cat "$GRACE_ANCHOR_FILE")))" -ge "$grace_seconds" ]
}

restart_container() {
    echo "antcode-healthcheck: 连续 $1 次探测失败（阈值 $max_failures），重启容器" >&2
    kill -TERM 1
    exit 1
}

previous=$(cat "$FAILURES_FILE" 2>/dev/null || echo 0)

# 上一轮探测卡死（被 Docker timeout 杀掉）时，重启决策落在这里执行。
if [ "$previous" -ge "$max_failures" ] && grace_elapsed; then
    restart_container "$previous"
fi

current=$((previous + 1))
echo "$current" >"$FAILURES_FILE"

if sh -c "$probe"; then
    echo 0 >"$FAILURES_FILE"
    exit 0
fi

if [ "$current" -ge "$max_failures" ] && grace_elapsed; then
    restart_container "$current"
fi
exit 1
