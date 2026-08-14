#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly BASE_COMPOSE="${SCRIPT_DIR}/docker-compose.prod.yml"
readonly BOOTSTRAP_COMPOSE="${SCRIPT_DIR}/docker-compose.prod.bootstrap-admin.yml"
readonly SOURCE_ENV_FILE="${1:?usage: bootstrap-admin.sh ENV_FILE}"
ENV_FILE=""

cleanup_env() {
    [[ -z "$ENV_FILE" ]] || rm -f "$ENV_FILE"
}

[[ -f "$SOURCE_ENV_FILE" ]] || { echo "environment file does not exist" >&2; exit 1; }
ENV_FILE="$(mktemp)"
trap cleanup_env EXIT
cp -- "$SOURCE_ENV_FILE" "$ENV_FILE"

base=(docker compose --env-file "$ENV_FILE" -f "$BASE_COMPOSE")
bootstrap=("${base[@]}" -f "$BOOTSTRAP_COMPOSE")

# migration 跑的是 Web API 镜像，它的 build 段挂在 web-api 服务上（一个镜像只有
# 一处构建定义），所以这里构建 web-api 而不是 migration。
"${base[@]}" build web-api
"${base[@]}" pull postgres redis
"${base[@]}" up -d --wait postgres redis
"${bootstrap[@]}" run --rm --no-deps migration
