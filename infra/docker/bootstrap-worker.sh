#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly BASE_COMPOSE="${SCRIPT_DIR}/docker-compose.prod.worker.yml"
readonly BOOTSTRAP_COMPOSE="${SCRIPT_DIR}/docker-compose.prod.bootstrap-worker.yml"
readonly SOURCE_ENV_FILE="${1:?usage: bootstrap-worker.sh ENV_FILE}"
readonly TIMEOUT_SECONDS="${ANTCODE_BOOTSTRAP_TIMEOUT_SECONDS:-180}"
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

"${SCRIPT_DIR}/verify-production-images.sh" "$ENV_FILE" --worker-only

cleanup() {
    "${bootstrap[@]}" stop worker
    "${bootstrap[@]}" rm -f worker
}
trap 'cleanup; cleanup_env' EXIT

"${bootstrap[@]}" up -d --no-deps worker
deadline=$((SECONDS + TIMEOUT_SECONDS))
while ((SECONDS < deadline)); do
    container_id="$("${bootstrap[@]}" ps -q worker)"
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id")"
    [[ "$status" == "healthy" ]] && break
    sleep 2
done
[[ "${status:-}" == "healthy" ]] || { echo "Worker bootstrap did not become healthy" >&2; exit 1; }

cleanup
trap cleanup_env EXIT
"${base[@]}" up -d --no-deps --wait worker
