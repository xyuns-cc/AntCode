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

"${SCRIPT_DIR}/verify-production-images.sh" "$ENV_FILE"
"${base[@]}" pull postgres redis migration
"${base[@]}" up -d --wait postgres redis
"${bootstrap[@]}" run --rm --no-deps migration
