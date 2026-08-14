#!/usr/bin/env bash
#
# 生产 Gateway 画像的可重复 E2E。
#
# 仓库没有自动化发布流水线，这个脚本就是生产画像 E2E 的**唯一**入口：同一套
# Compose 拓扑（docker-compose.prod*.yml）、同一套 PKI / 密钥生成实现
# （scripts/release_e2e_environment.py）、同一个编排器
# （scripts/release_e2e_orchestrator.py），五个应用镜像由 `docker compose build`
# 就地构建——与 deploy-production.sh 用的是同一份 build 段。
# 正式发布前必须在受控发布机上跑通它，见 docs/release-runbook.md 第 0 节。
#
# 覆盖：生成 mTLS PKI -> 起生产 Gateway 画像 -> 安装 Key 注册 -> 按分配到的
# worker_id 重签客户端证书 -> mTLS 连 Gateway -> 初始 Lease -> 心跳 -> 控制台
# online -> 跑 tests/e2e。
#
# 用法：
#   infra/docker/run-gateway-e2e.sh                 # 构建镜像后跑
#   ANTCODE_GATEWAY_E2E_SKIP_BUILD=1 …/run-gateway-e2e.sh   # 复用上一轮镜像
#   ANTCODE_GATEWAY_E2E_KEEP=1 …/run-gateway-e2e.sh         # 失败后保留栈排查
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${ANTCODE_GATEWAY_E2E_TAG:-gateway-e2e}"
STATE_DIR="${ANTCODE_GATEWAY_E2E_STATE_DIR:-/tmp/antcode-gateway-e2e}/run"
CONTROL_PROJECT="antcode-release-control"
WORKER_PROJECT="antcode-release-worker"
HTTPS_PORT="${ANTCODE_GATEWAY_E2E_HTTPS_PORT:-443}"
HTTP_REDIRECT_PORT="${ANTCODE_GATEWAY_E2E_HTTP_PORT:-80}"
GATEWAY_PORT="${ANTCODE_GATEWAY_E2E_GATEWAY_PORT:-15051}"
GIT_HTTP_PORT=18081
# WORKER_INSTALL_SOURCE_URL 只进安装脚本模板，E2E 期间没有任何进程去 clone 它，
# 但 web_api 的 worker_installer 会 fail-closed 校验它必须是 HTTPS Git 地址。
SOURCE_URL="${ANTCODE_GATEWAY_E2E_SOURCE_URL:-https://github.com/antcode/antcode.git}"

log() { printf '=== %s\n' "$*"; }

control_compose() {
  docker compose --env-file "$STATE_DIR/production.env" -p "$CONTROL_PROJECT" \
    -f "$ROOT/infra/docker/docker-compose.prod.yml" \
    -f "$ROOT/infra/docker/docker-compose.prod.e2e-control.yml" "$@"
}

worker_compose() {
  docker compose --env-file "$STATE_DIR/production.env" -p "$WORKER_PROJECT" \
    -f "$ROOT/infra/docker/docker-compose.prod.worker.yml" \
    -f "$ROOT/infra/docker/docker-compose.prod.e2e-worker.yml" "$@"
}

teardown() {
  local status=$?
  if [ -f "$STATE_DIR/production.env" ] && [ "$status" -ne 0 ]; then
    log "失败诊断"
    control_compose ps || true
    control_compose logs --no-color --tail=200 || true
    worker_compose logs --no-color --tail=200 || true
  fi
  # 保留模式同时服务于两种用途：失败后排查，以及把这套生产 Gateway 控制面
  # 借给物理机 Worker 验证（infra/docker/README.md 的部署矩阵）。Git HTTP 源也
  # 一并留着——tests/e2e 每个用例都要现场发布仓库，杀掉它等于把栈留成不可用状态。
  if [ "${ANTCODE_GATEWAY_E2E_KEEP:-0}" = "1" ]; then
    log "保留栈（ANTCODE_GATEWAY_E2E_KEEP=1）：$STATE_DIR"
    return "$status"
  fi
  if [ -f "$STATE_DIR/git-http.pid" ]; then
    kill "$(cat "$STATE_DIR/git-http.pid")" 2>/dev/null || true
  fi
  if [ -f "$STATE_DIR/production.env" ]; then
    worker_compose down -v --remove-orphans || true
    control_compose down -v --remove-orphans || true
  fi
  rm -rf "$STATE_DIR"
  return "$status"
}

preflight() {
  command -v docker >/dev/null || { echo "缺少 docker" >&2; exit 1; }
  command -v uv >/dev/null || { echo "缺少 uv（宿主需要 Python 3.11 + dev 依赖跑 pytest）" >&2; exit 1; }
  # Worker 沙箱走 bwrap 的非特权 user namespace。Ubuntu 24.04 起该内核开关默认
  # 为 1，会让容器内 bwrap 100% 起不来；这里只检测并报错，不代用户改内核参数。
  local knob=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
  if [ -r "$knob" ] && [ "$(cat "$knob")" != "0" ]; then
    echo "需要先放开非特权 user namespace: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" >&2
    exit 1
  fi
  if [ -e "$STATE_DIR" ]; then
    echo "上一轮状态目录残留，先清理: $STATE_DIR" >&2
    exit 1
  fi
}

resolve_source_ref() {
  if [ -n "${ANTCODE_GATEWAY_E2E_SOURCE_REF:-}" ]; then
    printf '%s' "$ANTCODE_GATEWAY_E2E_SOURCE_REF"
    return
  fi
  git -C "$ROOT" rev-parse HEAD 2>/dev/null && return
  echo "无法解析源码 revision（非 Git 工作树），请显式设置 ANTCODE_GATEWAY_E2E_SOURCE_REF" >&2
  exit 1
}

build_images() {
  if [ "${ANTCODE_GATEWAY_E2E_SKIP_BUILD:-0}" = "1" ]; then
    log "[2/6] 跳过构建，复用现有 :$TAG 镜像"
    return
  fi
  # 走生产 Compose 自己的 build 段，而不是另写一份 docker build：E2E 验的镜像与
  # deploy-production.sh 部署的镜像必须由同一处定义产出，否则两者会悄悄分叉。
  # control 栈里的 worker 在 co-located-worker profile 后面，compose build 不会碰它；
  # worker 镜像由 worker 栈构建，两边指向同一个 Dockerfile 与同一个 tag。
  log "[2/6] 用生产 Compose 的 build 段就地构建五个应用镜像"
  control_compose build
  worker_compose build
}

prepare_environment() {
  log "[1/6] 生成一次性 mTLS PKI、密钥与生产 Compose 环境"
  mkdir -p "$(dirname "$STATE_DIR")"
  chmod 0755 "$(dirname "$STATE_DIR")"
  uv run --frozen python -m scripts.prepare_local_release_e2e \
    --output-dir "$STATE_DIR" \
    --runtime-lock "$ROOT/infra/docker/release-runtime-images.json" \
    --image-tag "$TAG" \
    --source-url "$SOURCE_URL" \
    --source-ref "$(resolve_source_ref)" \
    --runner-env "$STATE_DIR.runner.env" \
    --https-port "$HTTPS_PORT" \
    --http-redirect-port "$HTTP_REDIRECT_PORT" \
    --gateway-port "$GATEWAY_PORT"
  mv "$STATE_DIR.runner.env" "$STATE_DIR/runner.env"
}

start_git_source() {
  log "[3/6] 起 E2E Git 源（宿主进程；地址取宿主出口 IP，宿主与 Worker 容器都能路由）"
  # host.docker.internal 只在容器里靠 extra_hosts 解析，宿主上的 pytest 解析不了；
  # 回环地址反过来只对宿主有效。所以 prepare 脚本按路由表取宿主出口 IP，两侧同址。
  local git_root
  git_root="$(grep '^ANTCODE_E2E_GIT_ROOT=' "$STATE_DIR/runner.env" | cut -d= -f2-)"
  nohup uv run --frozen python -m tests.e2e.git_http_server \
    --root "$git_root" --host 0.0.0.0 --port "$GIT_HTTP_PORT" \
    >"$STATE_DIR/git-http.log" 2>&1 &
  echo "$!" >"$STATE_DIR/git-http.pid"
  timeout 30 bash -c "until curl -fsS http://127.0.0.1:${GIT_HTTP_PORT}/ >/dev/null; do sleep 1; done"
}

start_stack() {
  log "[4/6] 起生产 Gateway 画像并用安装 Key 注册 Worker（含按 worker_id 重签客户端证书）"
  uv run --frozen python -m scripts.release_e2e_orchestrator \
    --environment "$STATE_DIR/production.env" \
    --state-dir "$STATE_DIR"
}

verify_transport() {
  log "[5/6] 校验 HTTPS 与 Gateway mTLS（含拒绝无客户端证书、拒绝 TLS<1.2）"
  uv run --frozen python -m scripts.verify_release_transport \
    --ca "$STATE_DIR/public-ca.crt" \
    --client-cert "$STATE_DIR/worker-tls/client.crt" \
    --client-key "$STATE_DIR/worker-tls/client.key" \
    --gateway-port "$GATEWAY_PORT"
}

run_e2e() {
  # 「初始 Lease -> 心跳 -> 控制台 online」不另设脚本重复断言：
  #   * 编排器最后一步 `up -d --wait` 等的是 Worker healthcheck /health/ready，
  #     它同时检查传输层连接与 lifecycle ready，Lease 没签发就不会 healthy；
  #   * 控制台 online + 心跳推进由 tests/e2e/test_worker_lifecycle.py 断言。
  log "[6/6] 跑 tests/e2e（生产 Gateway 画像）"
  set -a
  # shellcheck disable=SC1091
  . "$STATE_DIR/runner.env"
  ANTCODE_E2E_WORKER_ID="$(cat "$STATE_DIR/worker-id")"
  export ANTCODE_E2E_WORKER_ID
  set +a
  uv run --frozen --extra dev pytest tests/e2e -q
}

trap teardown EXIT
preflight
# 构建必须排在环境生成之后：compose build 要读 production.env 才能解析 ANTCODE_IMAGE_TAG
# 及其余 `${VAR:?}`，先构建的话 compose 直接以缺变量拒绝解析。
prepare_environment
build_images
start_git_source
start_stack
verify_transport

run_e2e
