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

# 五个应用镜像本地构建，第三方运行时镜像仍按 digest 拉取。两步都在停服之前完成：
# 构建失败或某个 pin 的 digest 拉不到时，正在跑的控制面一个容器都还没被动过。
#
# pull 必须点名这三个服务，不能用 `--ignore-buildable`：那个开关只跳过**自身声明了
# build 段**的服务，而 migration / crawl-redis-upgrade 跑的是 Web API 镜像却只引用
# 不构建，于是 compose 会拿 `antcode-web-api:<tag>` 去 registry 找，必然 403
# （真机实测）。点名的另一个好处是服务改名时 compose 直接报 "no such service"。
readonly RUNTIME_SERVICES=(postgres redis reverse-proxy)

# 停服集合必须与依赖图一致：边缘两层的 healthcheck 是**穿透式链路探针**
# （`docker-compose.prod.edge.yml:52-61,94-103`，frontend 打 web-api、reverse-proxy 打
# frontend），所以只要控制面被停，它们必然转 unhealthy。留着不停有两个真机实测后果：
#   1) 收尾的 `up -d --wait` 会撞上一个**早已 unhealthy**的 reverse-proxy —— compose 的
#      `--wait` 对 unhealthy 是快速失败，不等它恢复，于是整条 existing-upgrade --apply
#      在最后一步退出 1，而栈其实几十秒后自愈；
#   2) frontend 在 edge 网上是动态地址，被重建后 reverse-proxy 得靠 `resolver valid=10s`
#      重新解析（`nginx.prod.conf:19-25`），中间必然有一段解析失败窗口。
# 本来就是停机窗口，边缘一并停掉即可：`up -d --wait` 会按 depends_on 依次
# web-api → frontend → reverse-proxy 重新拉起，每层都带自己的 start_period。
readonly STOPPED_SERVICES=(reverse-proxy frontend web-api master gateway worker)

build_images() {
    "${compose[@]}" build
    "${compose[@]}" pull "${RUNTIME_SERVICES[@]}"
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
    compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
    build_images
    "${compose[@]}" stop --timeout "$STOP_TIMEOUT" "${STOPPED_SERVICES[@]}"
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

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
build_images
"${compose[@]}" stop --timeout "$STOP_TIMEOUT" "${STOPPED_SERVICES[@]}"
"${compose[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT" postgres redis
"${compose[@]}" run --rm --no-deps crawl-redis-upgrade \
    python -m scripts.migrate_crawl_redis \
    --mode "$UPGRADE_MODE" "${upgrade_arguments[@]}"

if [[ "$UPGRADE_MODE" == "existing-upgrade" && "$apply_requested" != true ]]; then
    printf '%s\n' 'dry-run passed; writers remain stopped. Review the report, then rerun with --apply --preflight-reviewed.'
    exit 0
fi

"${compose[@]}" run --rm --no-deps migration

"${compose[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT"
