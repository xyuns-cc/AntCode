#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.prod.worker.yml"
readonly SOURCE_ENV_FILE="${1:?usage: deploy-worker.sh ENV_FILE}"
readonly WAIT_TIMEOUT="${ANTCODE_DEPLOY_WAIT_TIMEOUT_SECONDS:-300}"
ENV_FILE=""

cleanup_env() {
    [[ -z "$ENV_FILE" ]] || rm -f "$ENV_FILE"
}

[[ -f "$SOURCE_ENV_FILE" ]] || { echo "environment file does not exist" >&2; exit 1; }
ENV_FILE="$(mktemp)"
trap cleanup_env EXIT
cp -- "$SOURCE_ENV_FILE" "$ENV_FILE"

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
# Worker 镜像本地构建：物理机 Worker 必须持有本仓源码，构建失败即部署中止，
# 不会去动正在跑的旧容器。
"${compose[@]}" build worker
"${compose[@]}" up -d --no-deps --wait --wait-timeout "$WAIT_TIMEOUT" worker
