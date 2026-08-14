#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.prod.yml"
readonly USAGE="usage: deploy-production.sh ENV_FILE {fresh-deploy|existing-upgrade|rotate-encryption-key} [arguments]"
readonly SOURCE_ENV_FILE="${1:?${USAGE}}"
readonly UPGRADE_MODE="${2:?${USAGE}}"
readonly WAIT_TIMEOUT="${ANTCODE_DEPLOY_WAIT_TIMEOUT_SECONDS:-300}"
readonly STOP_TIMEOUT="${ANTCODE_DEPLOY_STOP_TIMEOUT_SECONDS:-60}"
ENV_FILE=""

cleanup_env() {
    [[ -z "$ENV_FILE" ]] || rm -f "$ENV_FILE"
}

[[ -f "$SOURCE_ENV_FILE" ]] || { echo "environment file does not exist" >&2; exit 1; }
ENV_FILE="$(mktemp)"
trap cleanup_env EXIT
cp -- "$SOURCE_ENV_FILE" "$ENV_FILE"

case "$UPGRADE_MODE" in
    fresh-deploy | existing-upgrade | rotate-encryption-key) ;;
    *) printf 'invalid Crawl Redis upgrade mode: %s\n%s\n' "$UPGRADE_MODE" "$USAGE" >&2; exit 2 ;;
esac

shift 2
apply_requested=false
preflight_reviewed=false
writers_confirmed=false
upgrade_arguments=()
for argument in "$@"; do
    case "$argument" in
        --apply) apply_requested=true; upgrade_arguments+=("$argument") ;;
        --preflight-reviewed) preflight_reviewed=true ;;
        --confirm-writers-stopped) writers_confirmed=true; upgrade_arguments+=("$argument") ;;
        --mode | --mode=* | --url | --url=* | --namespace | --namespace=*)
            printf 'production deployment owns reserved argument: %s\n' "$argument" >&2
            exit 2
            ;;
        *) upgrade_arguments+=("$argument") ;;
    esac
done

if [[ "$UPGRADE_MODE" == "rotate-encryption-key" ]]; then
    if [[ "$writers_confirmed" != true ]]; then
        printf '%s\n' 'rotate-encryption-key requires --confirm-writers-stopped' >&2
        exit 2
    fi
    if [[ "${#upgrade_arguments[@]}" -ne 1 ]]; then
        printf '%s\n' 'rotate-encryption-key accepts only --confirm-writers-stopped' >&2
        exit 2
    fi
    "${SCRIPT_DIR}/verify-production-images.sh" "$ENV_FILE"
    compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
    "${compose[@]}" pull
    "${compose[@]}" stop --timeout "$STOP_TIMEOUT" web-api master gateway worker
    "${compose[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT" postgres redis
    "${compose[@]}" run --rm --no-deps migration \
        python -m scripts.rotate_encryption_key --confirm-writers-stopped
    "${compose[@]}" run --rm --no-deps migration \
        python -m scripts.rotate_encryption_key --apply --confirm-writers-stopped
    "${compose[@]}" run --rm --no-deps migration \
        python -m scripts.rotate_encryption_key --verify-primary-only --confirm-writers-stopped
    printf '%s\n' 'global encryption-key rotation verified; writers remain stopped for legacy keyring removal'
    exit 0
fi

if [[ "$UPGRADE_MODE" == "fresh-deploy" && "$apply_requested" == true ]]; then
    printf '%s\n' 'fresh-deploy is read-only and does not accept --apply' >&2
    exit 2
fi
if [[ "$UPGRADE_MODE" == "existing-upgrade" && "$writers_confirmed" != true ]]; then
    printf '%s\n' 'existing-upgrade requires --confirm-writers-stopped after every Redis writer is stopped' >&2
    exit 2
fi
if [[ "$apply_requested" == true && "$preflight_reviewed" != true ]]; then
    printf '%s\n' '--apply requires a prior dry-run and explicit --preflight-reviewed' >&2
    exit 2
fi
if [[ "$apply_requested" != true && "$preflight_reviewed" == true ]]; then
    printf '%s\n' '--preflight-reviewed is valid only together with --apply' >&2
    exit 2
fi

"${SCRIPT_DIR}/verify-production-images.sh" "$ENV_FILE"

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${compose[@]}" pull
"${compose[@]}" stop --timeout "$STOP_TIMEOUT" web-api master gateway worker
"${compose[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT" postgres redis
"${compose[@]}" run --rm --no-deps crawl-redis-upgrade \
    python -m scripts.migrate_crawl_redis \
    --mode "$UPGRADE_MODE" "${upgrade_arguments[@]}"

if [[ "$UPGRADE_MODE" == "existing-upgrade" && "$apply_requested" != true ]]; then
    printf '%s\n' 'dry-run passed; writers remain stopped. Review the report, then rerun with --apply --preflight-reviewed.'
    exit 0
fi

"${compose[@]}" run --rm --no-deps migration
"${compose[@]}" run --rm --no-deps migration \
    python -m scripts.migrate_worker_install_keys

"${compose[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT"
