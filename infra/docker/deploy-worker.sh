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

"${SCRIPT_DIR}/verify-production-images.sh" "$ENV_FILE" --worker-only

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${compose[@]}" pull worker
"${compose[@]}" up -d --no-deps --wait --wait-timeout "$WAIT_TIMEOUT" worker
