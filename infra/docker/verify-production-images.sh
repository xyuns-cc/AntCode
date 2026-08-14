#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.prod.yml"
readonly WORKER_COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.prod.worker.yml"
readonly RELEASE_COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.release.yml"
readonly ENV_FILE="${1:?usage: verify-production-images.sh ENV_FILE}"
readonly OIDC_ISSUER="${COSIGN_CERTIFICATE_OIDC_ISSUER:-https://token.actions.githubusercontent.com}"
readonly SERVICE_CERTIFICATE_IDENTITY="${COSIGN_SERVICE_CERTIFICATE_IDENTITY:?exact service signing workflow identity is required}"
readonly RELEASE_CERTIFICATE_IDENTITY="${COSIGN_RELEASE_CERTIFICATE_IDENTITY:?exact release collection workflow identity is required}"
readonly DIGEST_PATTERN='^.+@sha256:[0-9a-f]{64}$'
readonly CONTROL_SERVICES=(web-api master gateway frontend)
readonly RUNTIME_SERVICES=(postgres redis reverse-proxy)
readonly MODE="${2:-}"
TEMPORARY_DIR=""
CONTAINER_ID=""

fail() {
    echo "$1" >&2
    exit 1
}

cleanup() {
    if [[ -n "$CONTAINER_ID" ]]; then
        docker rm -f "$CONTAINER_ID" >/dev/null
    fi
    if [[ -n "$TEMPORARY_DIR" ]]; then
        rm -rf -- "$TEMPORARY_DIR"
    fi
}

require_commands() {
    local required
    for required in cosign docker jq; do
        command -v "$required" >/dev/null || fail "$required is required"
    done
}

verify_signature() {
    local image="$1"
    local identity="$2"
    cosign verify \
        --certificate-identity "$identity" \
        --certificate-oidc-issuer "$OIDC_ISSUER" \
        "$image" >/dev/null
}

resolve_release_contract() {
    docker compose --env-file "$ENV_FILE" -f "$RELEASE_COMPOSE_FILE" \
        --profile release-metadata config --format json
}

read_release_image() {
    jq -er '.services["release-collection"].image
        | select(test("^.+@sha256:[0-9a-f]{64}$"))' <<<"$1"
}

read_expected_revision() {
    jq -er '.services["release-collection"].labels["io.antcode.release.revision"]
        | select(test("^[0-9a-f]{40}$"))' <<<"$1"
}

inspect_revision() {
    docker image inspect \
        --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
        "$1"
}

validate_manifest() {
    local manifest="$1"
    local revision="$2"
    jq -e --arg revision "$revision" '
        type == "object" and
        keys == ["images", "revision", "schema_version"] and
        .schema_version == 2 and
        .revision == $revision and
        (.images | type == "object") and
        (.images | keys == [
            "frontend", "gateway", "master", "postgres", "redis",
            "reverse-proxy", "web-api", "worker"
        ]) and
        all(.images[]; type == "string" and test("^.+@sha256:[0-9a-f]{64}$"))
    ' "$manifest" >/dev/null || fail "release manifest contract validation failed"
}

extract_manifest() {
    local image="$1"
    local destination="$2"
    CONTAINER_ID="$(docker create "$image" /bin/true)"
    docker cp "${CONTAINER_ID}:/release-manifest.json" "$destination"
    docker rm -f "$CONTAINER_ID" >/dev/null
    CONTAINER_ID=""
}

resolve_service_image() {
    local service="$1"
    local compose_file="$COMPOSE_FILE"
    [[ "$service" == "worker" ]] && compose_file="$WORKER_COMPOSE_FILE"
    docker compose --env-file "$ENV_FILE" -f "$compose_file" config --images "$service"
}

verify_service() {
    local service="$1"
    local manifest="$2"
    local revision="$3"
    local image expected_image actual_revision
    image="$(resolve_service_image "$service")"
    [[ "$image" =~ $DIGEST_PATTERN ]] || fail "$service is not resolved to one exact sha256 digest"
    expected_image="$(jq -er --arg service "$service" '.images[$service]' "$manifest")"
    [[ "$image" == "$expected_image" ]] || fail "$service image does not belong to the selected release collection"
    verify_signature "$image" "$SERVICE_CERTIFICATE_IDENTITY"
    docker pull "$image" >/dev/null
    actual_revision="$(inspect_revision "$image")"
    [[ "$actual_revision" == "$revision" ]] || fail "$service image revision does not match the release collection"
    echo "verified release member: $service"
}

verify_runtime_service() {
    local service="$1"
    local manifest="$2"
    local image expected_image
    image="$(resolve_service_image "$service")"
    [[ "$image" =~ $DIGEST_PATTERN ]] || fail "$service is not resolved to one exact sha256 digest"
    expected_image="$(jq -er --arg service "$service" '.images[$service]' "$manifest")"
    [[ "$image" == "$expected_image" ]] || fail "$service image does not belong to the selected release collection"
    docker pull "$image" >/dev/null
    echo "verified release runtime member: $service"
}

verify_release_members() {
    local manifest="$1"
    local revision="$2"
    local service
    if [[ "$MODE" == "--worker-only" ]]; then
        verify_service worker "$manifest" "$revision"
        return
    fi
    for service in "${CONTROL_SERVICES[@]}"; do
        verify_service "$service" "$manifest" "$revision"
    done
    verify_service worker "$manifest" "$revision"
    for service in "${RUNTIME_SERVICES[@]}"; do
        verify_runtime_service "$service" "$manifest"
    done
}

main() {
    local contract release_image expected_revision actual_revision manifest
    require_commands
    [[ -f "$ENV_FILE" ]] || fail "environment file does not exist"
    [[ -z "$MODE" || "$MODE" == "--worker-only" ]] || fail "unsupported verification mode: $MODE"
    contract="$(resolve_release_contract)"
    release_image="$(read_release_image "$contract")"
    expected_revision="$(read_expected_revision "$contract")"
    verify_signature "$release_image" "$RELEASE_CERTIFICATE_IDENTITY"
    docker pull "$release_image" >/dev/null
    actual_revision="$(inspect_revision "$release_image")"
    [[ "$actual_revision" == "$expected_revision" ]] || fail "release collection OCI revision does not match the requested revision"
    TEMPORARY_DIR="$(mktemp -d)"
    trap cleanup EXIT
    manifest="${TEMPORARY_DIR}/release-manifest.json"
    extract_manifest "$release_image" "$manifest"
    validate_manifest "$manifest" "$expected_revision"
    verify_release_members "$manifest" "$expected_revision"
    echo "verified atomic release collection: $expected_revision"
}

main
