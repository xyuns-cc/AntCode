#!/usr/bin/env bash
set -euo pipefail

readonly USAGE="usage: verify-backup-restore.sh BACKUP_FILE RESTORE_DATABASE_URL EXPECTED_DATABASE -- MIGRATION_COMMAND [ARG...]"
readonly BACKUP_FILE="${1:?$USAGE}"
readonly RESTORE_DATABASE_URL="${2:?$USAGE}"
readonly EXPECTED_DATABASE="${3:?$USAGE}"
shift 3
[[ "${1:-}" == "--" ]] || { echo "$USAGE" >&2; exit 1; }
shift
readonly -a MIGRATION_COMMAND=("$@")
readonly CHECKSUM_FILE="${BACKUP_FILE}.sha256"
readonly RESTORE_TIMEOUT_SECONDS="${RESTORE_TIMEOUT_SECONDS:-3600}"
readonly MIGRATION_TIMEOUT_SECONDS="${MIGRATION_TIMEOUT_SECONDS:-3600}"
readonly POSTCHECK_TIMEOUT_SECONDS="${POSTCHECK_TIMEOUT_SECONDS:-300}"
readonly READINESS_TIMEOUT_SECONDS="${READINESS_TIMEOUT_SECONDS:-30}"
readonly RESTORE_READINESS_URL="${RESTORE_READINESS_URL:-}"
readonly SAFE_DATABASE_PATTERN='^antcode_restore_(test|ci)(_[a-z0-9][a-z0-9_-]*)?$'
readonly REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly SCHEMA_CHECK_SQL="$REPOSITORY_ROOT/infra/docker/verify-backup-schema.sql"
readonly DATA_CHECK_SQL="$REPOSITORY_ROOT/infra/docker/verify-backup-critical-data.sql"

die() {
    echo "restore verification refused: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null || die "$1 is required"
}

require_positive_integer() {
    local name="$1"
    local value="$2"
    [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "$name must be a positive integer"
}

validate_inputs() {
    [[ "$EXPECTED_DATABASE" =~ $SAFE_DATABASE_PATTERN ]] ||
        die "target name must match antcode_restore_test_* or antcode_restore_ci_*"
    [[ ${#MIGRATION_COMMAND[@]} -gt 0 ]] || die "a real migration command is required after --"
    [[ -f "$BACKUP_FILE" && -f "$CHECKSUM_FILE" ]] || die "backup and checksum files are required"
    require_positive_integer RESTORE_TIMEOUT_SECONDS "$RESTORE_TIMEOUT_SECONDS"
    require_positive_integer MIGRATION_TIMEOUT_SECONDS "$MIGRATION_TIMEOUT_SECONDS"
    require_positive_integer POSTCHECK_TIMEOUT_SECONDS "$POSTCHECK_TIMEOUT_SECONDS"
    require_positive_integer READINESS_TIMEOUT_SECONDS "$READINESS_TIMEOUT_SECONDS"
    for command_name in pg_restore psql sha256sum timeout; do
        require_command "$command_name"
    done
    [[ -z "$RESTORE_READINESS_URL" ]] || require_command curl
}

verify_database_identity() {
    local actual_database
    actual_database="$(PGDATABASE="$RESTORE_DATABASE_URL" timeout "$POSTCHECK_TIMEOUT_SECONDS" psql \
        --no-psqlrc --quiet --tuples-only --no-align \
        --set=ON_ERROR_STOP=1 --command='SELECT current_database()')"
    [[ "$actual_database" == "$EXPECTED_DATABASE" ]] ||
        die "connected database '$actual_database' does not equal expected '$EXPECTED_DATABASE'"
}

verify_checksum() {
    local -a checksum_lines=()
    local checksum_line
    local expected_hash expected_name extra
    while IFS= read -r checksum_line || [[ -n "$checksum_line" ]]; do
        checksum_lines[${#checksum_lines[@]}]="$checksum_line"
    done < "$CHECKSUM_FILE"
    [[ ${#checksum_lines[@]} -eq 1 ]] || die "checksum file must contain exactly one record"
    read -r expected_hash expected_name extra <<<"${checksum_lines[0]}"
    [[ ${#expected_hash} -eq 64 && ! "$expected_hash" =~ [^0-9a-f] && -z "${extra:-}" ]] ||
        die "checksum record format is invalid"
    [[ "$expected_name" == "$(basename "$BACKUP_FILE")" ]] ||
        die "checksum record is not bound to the requested backup"
    (cd "$(dirname "$BACKUP_FILE")" && sha256sum -c -- "$(basename "$CHECKSUM_FILE")")
}

restore_backup() {
    verify_checksum
    PGDATABASE="$RESTORE_DATABASE_URL" timeout "$RESTORE_TIMEOUT_SECONDS" pg_restore \
        --clean \
        --if-exists \
        --exit-on-error \
        --single-transaction \
        --no-owner \
        --no-privileges \
        -- "$BACKUP_FILE"
}

run_migration() {
    echo "running the required post-restore migration command"
    (
        cd "$REPOSITORY_ROOT"
        DATABASE_URL="$RESTORE_DATABASE_URL" \
            timeout "$MIGRATION_TIMEOUT_SECONDS" "${MIGRATION_COMMAND[@]}"
    )
}

verify_schema() {
    PGDATABASE="$RESTORE_DATABASE_URL" timeout "$POSTCHECK_TIMEOUT_SECONDS" psql \
        --no-psqlrc --quiet --set=ON_ERROR_STOP=1 --file="$SCHEMA_CHECK_SQL"
}

verify_critical_data() {
    PGDATABASE="$RESTORE_DATABASE_URL" timeout "$POSTCHECK_TIMEOUT_SECONDS" psql \
        --no-psqlrc --quiet --tuples-only --no-align \
        --set=ON_ERROR_STOP=1 --file="$DATA_CHECK_SQL"
}

verify_application_readiness() {
    if [[ -z "$RESTORE_READINESS_URL" ]]; then
        echo "database restore drill passed; application readiness NOT VERIFIED (RESTORE_READINESS_URL is unset)"
        return
    fi
    timeout "$READINESS_TIMEOUT_SECONDS" curl --fail --silent --show-error \
        --max-time "$READINESS_TIMEOUT_SECONDS" "$RESTORE_READINESS_URL" >/dev/null
    echo "restore drill passed: database migration/schema/data and application readiness verified"
}

main() {
    validate_inputs
    verify_database_identity
    restore_backup
    run_migration
    verify_database_identity
    verify_schema
    verify_critical_data
    verify_application_readiness
}

main "$@"
