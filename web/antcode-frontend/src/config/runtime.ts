const DEFAULT_API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const DEFAULT_WS_BASE_URL =
  import.meta.env.VITE_WS_BASE_URL ||
  DEFAULT_API_BASE_URL.replace(/^http/i, (match) => (match.toLowerCase() === 'https' ? 'wss' : 'ws'))
const DEFAULT_APP_TITLE = import.meta.env.VITE_APP_TITLE || 'AntCode 任务调度平台'
const DEFAULT_APP_VERSION = import.meta.env.VITE_APP_VERSION || '1.0.0'

export interface RuntimeConfig {
  API_BASE_URL: string
  WS_BASE_URL: string
  APP_TITLE: string
  APP_VERSION: string
}

declare global {
  interface Window {
    __ANTCODE_CONFIG__?: Partial<RuntimeConfig>
  }
}

const runtimeOverrides: Partial<RuntimeConfig> =
  typeof window !== 'undefined' ? window.__ANTCODE_CONFIG__ || {} : {}

const defaultConfig: RuntimeConfig = {
  API_BASE_URL: DEFAULT_API_BASE_URL,
  WS_BASE_URL: DEFAULT_WS_BASE_URL,
  APP_TITLE: DEFAULT_APP_TITLE,
  APP_VERSION: DEFAULT_APP_VERSION,
}

export const RUNTIME_CONFIG: RuntimeConfig = {
  ...defaultConfig,
  ...runtimeOverrides,
}
