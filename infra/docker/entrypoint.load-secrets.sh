#!/bin/sh
set -eu

load_secret() {
    variable="$1"
    file_variable="${variable}_FILE"
    eval "secret_file=\${${file_variable}:-}"
    [ -n "$secret_file" ] || return 0

    eval "inline_value=\${${variable}:-}"
    if [ -n "$inline_value" ]; then
        echo "$variable and $file_variable cannot both be configured" >&2
        exit 1
    fi
    if [ ! -f "$secret_file" ] || [ ! -r "$secret_file" ]; then
        echo "$file_variable is not a readable regular file" >&2
        exit 1
    fi

    secret_value="$(cat "$secret_file")"
    export "$variable=$secret_value"
    unset "$file_variable"
}

for variable in \
    DATABASE_URL \
    REDIS_URL \
    ENCRYPTION_KEY \
    ENCRYPTION_KEY_SALT \
    ENCRYPTION_KEYS_LEGACY \
    DEFAULT_ADMIN_PASSWORD \
    ANTCODE_WORKER_KEY
do
    load_secret "$variable"
done

exec "$@"
