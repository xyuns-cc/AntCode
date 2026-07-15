import { APP_TITLE, APP_BRAND_NAME, PLATFORM_TITLE, APP_LOGO_ICON, APP_LOGO_SHORT } from '@/config/app'
import { resolveApiBaseUrl } from '@/utils/apiEndpoint'

const browserHostname = typeof window === 'undefined' ? '' : window.location.hostname
const browserProtocol = typeof window === 'undefined' ? 'http' : window.location.protocol

export const API_BASE_URL = resolveApiBaseUrl({
  explicitApiBaseUrl: import.meta.env.VITE_API_BASE_URL,
  serverPort: import.meta.env.SERVER_PORT,
  bindHost: import.meta.env.BIND_HOST,
  serverDomain: import.meta.env.SERVER_DOMAIN,
  pageHostname: browserHostname,
  protocol: browserProtocol,
})

// 应用配置
export { APP_TITLE, APP_BRAND_NAME, PLATFORM_TITLE, APP_LOGO_ICON, APP_LOGO_SHORT }
export const APP_VERSION = '1.0.0'

// 项目类型
export const PROJECT_TYPES = {
  FILE: 'file',
  RULE: 'rule',
  CODE: 'code',
} as const

// 项目状态
export const PROJECT_STATUS = {
  ACTIVE: 'active',
  INACTIVE: 'inactive',
  ERROR: 'error',
} as const

// 任务状态
export const TASK_STATUS = {
  PENDING: 'pending',
  RUNNING: 'running',
  SUCCESS: 'success',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
} as const

// 日志类型
export const LOG_TYPES = {
  STDOUT: 'stdout',
  STDERR: 'stderr',
} as const

// 分页配置
export const PAGINATION = {
  DEFAULT_PAGE_SIZE: 10,
  PAGE_SIZE_OPTIONS: ['10', '20', '50', '100'],
} as const

// 本地存储键名
export const STORAGE_KEYS = {
  ACCESS_TOKEN: 'access_token',
  REFRESH_TOKEN: 'refresh_token',
  USER_INFO: 'user_info',
  INSTALL_KEY_ALLOWED_SOURCE: 'install_key_allowed_source',
  REMEMBER_USERNAME: 'remember_username',
  // P1-07: REMEMBER_PASSWORD 常量保留仅为兼容清理旧值(Login/index.tsx 会 removeItem 旧 key),
  // 严禁再用来写入密码。前端已删除所有 SetItem 调用。
  REMEMBER_PASSWORD: 'remember_password',
  REMEMBER_ME: 'remember_me',
} as const
