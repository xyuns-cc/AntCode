#!/usr/bin/env bash
#
# 生产 Gateway 画像的可重复 E2E。
#
# 仓库没有自动化发布流水线，这个脚本就是生产画像 E2E 的**唯一**入口：同一套
# Compose 拓扑（docker-compose.prod*.yml）、同一套 PKI / 密钥生成实现
# （scripts/release_e2e_environment.py）、同一个编排器
# （scripts/release_e2e_orchestrator.py），五个应用镜像由 `docker compose build`
# 就地构建——与 deploy-production.sh 用的是同一份 build 段。
# 正式发布前必须在受控发布机（有 Docker 与中间件）上跑通它；make release-gate 不覆盖。
#
# 覆盖：生成 mTLS PKI -> 起生产 Gateway 画像 -> 安装 Key 注册 -> 按分配到的
# worker_id 重签客户端证书 -> mTLS 连 Gateway -> TLS 材料热更新（不重启换证 /
# CA 级吊销）-> 初始 Lease -> 心跳 -> 控制台 online -> 跑 tests/e2e。
#
# Worker 是"每主机一个进程"的部署单位（prod.worker.yml 的容器名/数据卷/mTLS 目录
# 全由 env 参数化），所以多 Worker = 同一份 Worker Compose 起多个 project，各自一张
# 以自身 worker_id 为 CN 的客户端证书。
#
# 用法：
#   infra/docker/run-gateway-e2e.sh                 # 构建镜像后跑
#   ANTCODE_GATEWAY_E2E_WORKERS=2 …/run-gateway-e2e.sh      # 起 2 个 mTLS Worker
#   ANTCODE_GATEWAY_E2E_SKIP_BUILD=1 …/run-gateway-e2e.sh   # 复用上一轮镜像，仅供调试，不是发布门禁
#   ANTCODE_GATEWAY_E2E_KEEP=1 …/run-gateway-e2e.sh         # 失败后保留栈排查
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${ANTCODE_GATEWAY_E2E_TAG:-gateway-e2e}"
STATE_DIR="${ANTCODE_GATEWAY_E2E_STATE_DIR:-/tmp/antcode-gateway-e2e}/run"
CONTROL_PROJECT="antcode-release-control"
WORKER_PROJECT="antcode-release-worker"
WORKERS="${ANTCODE_GATEWAY_E2E_WORKERS:-1}"
ENVIRONMENT_FILE="$STATE_DIR/production.env"
# WORKER_INSTALL_SOURCE_URL 只进安装脚本模板，E2E 期间没有任何进程去 clone 它，
# 但 web_api 的 worker_installer 会 fail-closed 校验它必须是 HTTPS Git 地址。
SOURCE_URL="${ANTCODE_GATEWAY_E2E_SOURCE_URL:-https://github.com/antcode/antcode.git}"

# 端口默认值只在 scripts/release_e2e_environment.py 定义一份（[3/7] 已为 18081 立过这条
# 规矩），这里只在调用方显式覆盖时才把值透传给 prepare。此后每一步用的端口都从
# production.env 读回，谁也不再自带 443 / 80 / 15051。
PORT_OVERRIDES=()
[ -z "${ANTCODE_GATEWAY_E2E_HTTPS_PORT:-}" ] || PORT_OVERRIDES+=(--https-port "$ANTCODE_GATEWAY_E2E_HTTPS_PORT")
[ -z "${ANTCODE_GATEWAY_E2E_HTTP_PORT:-}" ] || PORT_OVERRIDES+=(--http-redirect-port "$ANTCODE_GATEWAY_E2E_HTTP_PORT")
[ -z "${ANTCODE_GATEWAY_E2E_GATEWAY_PORT:-}" ] || PORT_OVERRIDES+=(--gateway-port "$ANTCODE_GATEWAY_E2E_GATEWAY_PORT")

log() { printf '=== %s\n' "$*"; }

# 所有宿主侧 Python 都必须用这一组 flag，与 `make install` 的
# `uv sync --all-packages --extra dev` 完全一致：
#   * --all-packages：根 pyproject 的 [project].dependencies **不依赖任何 workspace
#     成员**，缺了它 `uv run` 会把 venv 同步成"只有第三方依赖"，编排器与 tests/e2e
#     里的 `import antcode_core / antcode_contracts` 一律 ModuleNotFoundError；
#   * --extra dev：编排器复用 tests/e2e 的 HTTP helper，helpers.py 经 conftest 依赖 pytest。
# 每次 `uv run` 都会按给定 flag 重新同步 venv，所以这里必须处处一致——一处漏掉就会把
# 上一步装好的包卸掉，故障点还落在下一步（真机实测：卡在 [4/7] 与 tests/e2e teardown）。
UV_RUN=(uv run --frozen --all-packages --extra dev)

control_compose() {
  docker compose --env-file "$ENVIRONMENT_FILE" -p "$CONTROL_PROJECT" \
    -f "$ROOT/infra/docker/docker-compose.prod.yml" \
    -f "$ROOT/infra/docker/docker-compose.prod.e2e-control.yml" "$@"
}

# 第 0 个 Worker 沿用原 project 名，其余按索引加后缀——与
# scripts/release_e2e_compose.py::worker_project 必须保持一致，否则清理会漏掉容器。
# 容器名 / 数据卷 / mTLS 目录逐 Worker 不同，取值由 prepare 阶段写出的 worker-N.env
# 提供（shell 环境优先级高于 --env-file），命名规则只在 Python 侧定义一份。
worker_compose() {
  local index="$1"; shift
  local project="$WORKER_PROJECT"
  [ "$index" -eq 0 ] || project="$WORKER_PROJECT-$index"
  (
    set -a
    # shellcheck disable=SC1090
    . "$STATE_DIR/worker-$index.env"
    set +a
    docker compose --env-file "$ENVIRONMENT_FILE" -p "$project" \
      -f "$ROOT/infra/docker/docker-compose.prod.worker.yml" \
      -f "$ROOT/infra/docker/docker-compose.prod.e2e-worker.yml" "$@"
  )
}

worker_indexes() {
  seq 0 "$((WORKERS - 1))"
}

teardown() {
  local status=$?
  if [ -f "$ENVIRONMENT_FILE" ] && [ "$status" -ne 0 ]; then
    log "失败诊断"
    control_compose ps || true
    control_compose logs --no-color --tail=200 || true
    for index in $(worker_indexes); do
      worker_compose "$index" logs --no-color --tail=200 || true
    done
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
  if [ -f "$ENVIRONMENT_FILE" ]; then
    for index in $(worker_indexes); do
      worker_compose "$index" down -v --remove-orphans || true
    done
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
  if ! [ "$WORKERS" -ge 1 ] 2>/dev/null; then
    echo "ANTCODE_GATEWAY_E2E_WORKERS 必须是 >=1 的整数: $WORKERS" >&2
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
  local reused=()
  if [ "${ANTCODE_GATEWAY_E2E_SKIP_BUILD:-0}" = "1" ]; then
    log "[2/7] 跳过构建，复用现有 :$TAG 镜像"
    reused=(--reused)
  else
    # 走生产 Compose 自己的 build 段，而不是另写一份 docker build：E2E 验的镜像与
    # deploy-production.sh 部署的镜像必须由同一处定义产出，否则两者会悄悄分叉。
    # control 栈里的 worker 在 co-located-worker profile 后面，compose build 不会碰它；
    # worker 镜像由 worker 栈构建，两边指向同一个 Dockerfile 与同一个 tag。
    log "[2/7] 用生产 Compose 的 build 段就地构建五个应用镜像"
    control_compose build
    worker_compose 0 build
  fi
  # 记下五个镜像的 image ID 与创建时间：[4/7] 会要求 Compose 解析出的引用仍指向同一个
  # ID。原来那条"镜像名对得上"的断言两边同源、恒真，验不出复用的是不是旧代码。
  "${UV_RUN[@]}" python -m scripts.release_e2e_provenance \
    --state-dir "$STATE_DIR" ${reused[@]+"${reused[@]}"}
}

prepare_environment() {
  log "[1/7] 生成一次性 mTLS PKI、密钥与生产 Compose 环境"
  mkdir -p "$(dirname "$STATE_DIR")"
  chmod 0755 "$(dirname "$STATE_DIR")"
  "${UV_RUN[@]}" python -m scripts.prepare_local_release_e2e \
    --output-dir "$STATE_DIR" \
    --runtime-lock "$ROOT/infra/docker/release-runtime-images.json" \
    --image-tag "$TAG" \
    --source-url "$SOURCE_URL" \
    --source-ref "$(resolve_source_ref)" \
    --runner-env "$STATE_DIR.runner.env" \
    --workers "$WORKERS" ${PORT_OVERRIDES[@]+"${PORT_OVERRIDES[@]}"}
  mv "$STATE_DIR.runner.env" "$STATE_DIR/runner.env"
}

runner_value() {
  grep "^$1=" "$STATE_DIR/runner.env" | cut -d= -f2-
}

start_git_source() {
  log "[3/7] 起 E2E Git 源（宿主进程；地址取宿主出口 IP，宿主与 Worker 容器都能路由）"
  # host.docker.internal 只在容器里靠 extra_hosts 解析，宿主上的 pytest 解析不了；
  # 回环地址反过来只对宿主有效。所以 prepare 脚本按路由表取宿主出口 IP，两侧同址。
  #
  # 启动与自证都交给 scripts.start_e2e_git_source，这一步才有真实退出码可读：
  # 原先是 `nohup … &` 加 `curl` 探活，前者的退出码无人读，后者只证明"端口上有人
  # 应答"。18081 被上一轮遗留的孤儿占着时，新进程 EADDRINUSE 秒死而探活对着孤儿
  # 成功，整轮 E2E 于是验错对象还报全过（测试机已复现）。
  # 探活地址直接用 ANTCODE_E2E_GIT_BASE_URL——tests/e2e 连的就是它，端口也只在
  # scripts/release_e2e_environment.py 定义一份，这里不再另抄一个 18081。
  "${UV_RUN[@]}" python -m scripts.start_e2e_git_source \
    --root "$(runner_value ANTCODE_E2E_GIT_ROOT)" \
    --base-url "$(runner_value ANTCODE_E2E_GIT_BASE_URL)" \
    --log "$STATE_DIR/git-http.log" \
    --pid-file "$STATE_DIR/git-http.pid"
}

start_stack() {
  log "[4/7] 起生产 Gateway 画像并用安装 Key 注册 $WORKERS 个 Worker（含按 worker_id 重签客户端证书）"
  "${UV_RUN[@]}" python -m scripts.release_e2e_orchestrator \
    --environment "$ENVIRONMENT_FILE" \
    --state-dir "$STATE_DIR"
}

verify_transport() {
  log "[5/7] 校验 HTTPS 与 Gateway mTLS（拒绝无客户端证书、拒绝 TLS<1.2、拒绝证书冒充）"
  # 多 Worker 拓扑下额外用第二个**真实**已注册 Worker 做冒充负向用例；单 Worker 时
  # 只有合成身份可用（脚本内恒验），peer 传空串。
  local peer
  peer="$("${UV_RUN[@]}" python -c \
    'import json,sys; ids=json.load(open(sys.argv[1])); print(ids[1] if len(ids)>1 else "")' \
    "$STATE_DIR/worker-ids.json")"
  # origin 与 Gateway 端口全部从 production.env 推导：本轮 Compose 发布的就是那三个
  # 端口，脚本里再抄一份默认值，判据就会打在共享测试机上别人的 :443 / :80 上——那套
  # 栈设的正是同一组安全头，"连通 + 头对"根本区分不出被验对象是谁。
  "${UV_RUN[@]}" python -m scripts.verify_release_transport \
    --environment "$ENVIRONMENT_FILE" \
    --ca "$STATE_DIR/public-ca.crt" \
    --client-cert "$STATE_DIR/worker-tls/client.crt" \
    --client-key "$STATE_DIR/worker-tls/client.key" \
    --worker-id "$(cat "$STATE_DIR/worker-id")" \
    --peer-worker-id "$peer"
}

verify_tls_hot_reload() {
  log "[6/7] 校验 TLS 材料热更新（换盘即生效、旧材料被拒、Gateway 不重启）"
  # 必须对着**真在跑的容器**验：单测里的热更新跑在宿主进程的 grpc.aio.server 上，
  # 生产画像下 gRPC 到底会不会在每次新握手前回调 fetcher，只有这里能证伪。
  # 校验自己会把材料写回原值，之后的 tests/e2e 不受影响。
  local gateway_container
  gateway_container="$(control_compose ps -q gateway)"
  [ -n "$gateway_container" ] || { echo "找不到 gateway 容器，无法校验热更新" >&2; exit 1; }
  "${UV_RUN[@]}" python -m scripts.verify_gateway_tls_hot_reload \
    --environment "$ENVIRONMENT_FILE" \
    --gateway-tls-dir "$STATE_DIR/gateway-tls" \
    --ca-cert "$STATE_DIR/public-ca.crt" \
    --ca-key "$STATE_DIR/ca.key" \
    --client-cert "$STATE_DIR/worker-tls/client.crt" \
    --client-key "$STATE_DIR/worker-tls/client.key" \
    --gateway-container "$gateway_container"
}

run_e2e() {
  # 「初始 Lease -> 心跳 -> 控制台 online」不另设脚本重复断言：
  #   * 编排器最后一步 `up -d --wait` 等的是 Worker healthcheck /health/ready，
  #     它同时检查传输层连接与 lifecycle ready，Lease 没签发就不会 healthy；
  #   * 控制台 online + 心跳推进由 tests/e2e/test_worker_lifecycle.py 断言。
  log "[7/7] 跑 tests/e2e（生产 Gateway 画像）"
  set -a
  # shellcheck disable=SC1091
  . "$STATE_DIR/runner.env"
  ANTCODE_E2E_WORKER_ID="$(cat "$STATE_DIR/worker-id")"
  export ANTCODE_E2E_WORKER_ID
  set +a
  "${UV_RUN[@]}" pytest tests/e2e -q
}

# 复用镜像的那一轮与完整构建的那一轮，通过与否的含义不同，最后一行必须说清楚是哪一种：
# 镜像与提交之间在不改 Dockerfile 的前提下没有可验证的绑定，所以复用轮次只能当调试用。
report_verdict() {
  if [ "${ANTCODE_GATEWAY_E2E_SKIP_BUILD:-0}" = "1" ]; then
    log "调试轮次 7/7 通过：镜像未经本轮构建，**不构成发布门禁**；发布前请去掉 ANTCODE_GATEWAY_E2E_SKIP_BUILD 重跑"
    return
  fi
  log "发布门禁 7/7 通过：五个应用镜像由本轮源码树构建，各步判据均打在本轮发布的端口上"
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
verify_tls_hot_reload

run_e2e
report_verdict
