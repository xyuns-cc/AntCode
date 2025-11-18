#!/bin/sh
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/app}

generate_runtime_config() {
  APP_DIR="$APP_DIR" node <<'NODE'
    const fs = require('fs')
    const path = require('path')

    const resolveEnv = (key, fallback) => {
      const value =
        process.env[key] ||
        process.env[`VITE_${key}`] ||
        process.env[`FRONTEND_${key}`]
      return value && value.trim().length > 0 ? value : fallback
    }

    const config = {
      API_BASE_URL: resolveEnv('API_BASE_URL', 'http://localhost:8000'),
      WS_BASE_URL: resolveEnv('WS_BASE_URL', 'ws://localhost:8000'),
      APP_TITLE: resolveEnv('APP_TITLE', 'AntCode 任务调度平台'),
      APP_VERSION: resolveEnv('APP_VERSION', '1.0.0'),
    }

    const targetPath = path.join(process.env.APP_DIR || '/opt/app', 'env-config.js')
    const content = `window.__ANTCODE_CONFIG__ = ${JSON.stringify(config, null, 2)};\n`
    fs.writeFileSync(targetPath, content, 'utf8')
    console.log('Generated runtime config at', targetPath)
  NODE
}

generate_runtime_config

exec "$@"
