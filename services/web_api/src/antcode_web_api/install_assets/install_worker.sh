#!/usr/bin/env bash
set -euo pipefail

readonly MIN_PYTHON_MINOR=11
readonly DEFAULT_WORKER_PORT=8001
readonly DEFAULT_WORKER_NAME="Worker-001"

die() {
  printf 'AntCode Worker install failed: %s\n' "$1" >&2
  exit 1
}

require_env() {
  local name="$1"
  [ -n "${!name:-}" ] || die "required environment variable $name is missing"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command $1 was not found"
}

validate_inputs() {
  require_env ANTCODE_WORKER_KEY
  require_env WORKER_API_BASE_URL
  require_env WORKER_GATEWAY_ENDPOINT
  require_env WORKER_INSTALL_SOURCE_URL
  require_env WORKER_INSTALL_SOURCE_REF
  require_env WORKER_INSTALL_UV_VERSION
  [[ "$WORKER_INSTALL_SOURCE_URL" == https://* ]] || die "source URL must use HTTPS"
  [[ "$WORKER_INSTALL_SOURCE_REF" =~ ^[0-9a-fA-F]{40}$ ]] || die "source ref must be a full Git commit"
  [[ "$WORKER_INSTALL_UV_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "uv version must be pinned"
  case "$WORKER_API_BASE_URL" in
    https://* | http://localhost | http://localhost:* | http://127.0.0.1 | http://127.0.0.1:*) ;;
    *) die "remote API base URL must use HTTPS" ;;
  esac
}

resolve_python() {
  require_command python3
  python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, ${MIN_PYTHON_MINOR}) else 1)" \
    || die "Python 3.${MIN_PYTHON_MINOR} or newer is required"
  python3 -m pip --version >/dev/null 2>&1 || die "Python pip is required"
  printf '%s\n' "$(command -v python3)"
}

install_uv() {
  local python_bin="$1"
  local bootstrap_dir="$2"
  local uv_bin="$bootstrap_dir/bin/uv"
  if [ ! -x "$uv_bin" ]; then
    "$python_bin" -m venv "$bootstrap_dir"
    "$bootstrap_dir/bin/python" -m pip install "uv==$WORKER_INSTALL_UV_VERSION" >&2
  fi
  "$uv_bin" --version >/dev/null
  printf '%s\n' "$uv_bin"
}

checkout_source() {
  local install_dir="$1"
  local temp_dir="${install_dir}.tmp.$$"
  [ ! -e "$install_dir" ] || die "install directory already exists: $install_dir"
  [ ! -e "$temp_dir" ] || die "temporary install directory already exists: $temp_dir"
  (
    trap 'rm -rf "$temp_dir"' EXIT
    git init --quiet "$temp_dir"
    git -C "$temp_dir" remote add origin "$WORKER_INSTALL_SOURCE_URL"
    git -C "$temp_dir" fetch --quiet --depth 1 origin "$WORKER_INSTALL_SOURCE_REF"
    local actual_ref expected_ref
    actual_ref="$(git -C "$temp_dir" rev-parse FETCH_HEAD | tr '[:upper:]' '[:lower:]')"
    expected_ref="$(printf '%s' "$WORKER_INSTALL_SOURCE_REF" | tr '[:upper:]' '[:lower:]')"
    [ "$actual_ref" = "$expected_ref" ] || die "fetched source commit does not match pinned ref"
    git -C "$temp_dir" checkout --quiet --detach FETCH_HEAD
    mv "$temp_dir" "$install_dir"
  )
}

write_worker_config() {
  local install_dir="$1"
  local config_file="$install_dir/.antcode-worker.env"
  umask 077
  {
    printf 'WORKER_API_BASE_URL=%q\n' "$WORKER_API_BASE_URL"
    printf 'WORKER_GATEWAY_ENDPOINT=%q\n' "$WORKER_GATEWAY_ENDPOINT"
    printf 'WORKER_GATEWAY_TLS=%q\n' "${WORKER_GATEWAY_TLS:-false}"
    printf 'WORKER_CREDENTIAL_STORE=%q\n' "persistent"
    printf 'WORKER_NAME=%q\n' "${WORKER_NAME:-$DEFAULT_WORKER_NAME}"
    printf 'WORKER_PORT=%q\n' "${WORKER_PORT:-$DEFAULT_WORKER_PORT}"
  } >"$config_file"
  chmod 600 "$config_file"
}

start_worker() {
  local install_dir="$1"
  local uv_bin="$2"
  local worker_port="${WORKER_PORT:-$DEFAULT_WORKER_PORT}"
  [[ "$worker_port" =~ ^[0-9]+$ ]] || die "WORKER_PORT must be an integer"
  ((worker_port >= 1 && worker_port <= 65535)) || die "WORKER_PORT must be between 1 and 65535"
  cd "$install_dir"
  export WORKER_CREDENTIAL_STORE="${WORKER_CREDENTIAL_STORE:-persistent}"
  export WORKER_API_BASE_URL WORKER_GATEWAY_ENDPOINT WORKER_GATEWAY_TLS
  exec "$uv_bin" run --frozen python -m antcode_worker run \
    --name "${WORKER_NAME:-$DEFAULT_WORKER_NAME}" \
    --port "$worker_port" \
    --transport gateway \
    --gateway-endpoint "$WORKER_GATEWAY_ENDPOINT" \
    --worker-key "$ANTCODE_WORKER_KEY"
}

main() {
  validate_inputs
  require_command git
  local install_root="${WORKER_INSTALL_ROOT:-$HOME/.antcode}"
  local install_dir="$install_root/worker-src"
  mkdir -p "$install_root"
  local python_bin
  python_bin="$(resolve_python)"
  local uv_bin
  uv_bin="$(install_uv "$python_bin" "$install_root/uv-bootstrap")"
  checkout_source "$install_dir"
  "$uv_bin" sync --directory "$install_dir" --all-packages --frozen
  write_worker_config "$install_dir"
  start_worker "$install_dir" "$uv_bin"
}

main "$@"
