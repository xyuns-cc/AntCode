import { APP_TITLE, APP_BRAND_NAME, PLATFORM_TITLE, APP_LOGO_ICON, APP_LOGO_SHORT } from '@/config/app'

// API 地址（从环境变量读取，默认本地开发地址）
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
// WS 地址（从 API 地址自动推导：http -> ws, https -> wss）
export const WS_BASE_URL = API_BASE_URL.replace(/^http/, 'ws')

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

// WebSocket 连接状态
export const WS_CONNECTION_STATUS = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  ERROR: 'error',
} as const

// 本地存储键名
export const STORAGE_KEYS = {
  ACCESS_TOKEN: 'access_token',
  REFRESH_TOKEN: 'refresh_token',
  USER_INFO: 'user_info',
  REMEMBER_USERNAME: 'remember_username',
  REMEMBER_PASSWORD: 'remember_password',
  REMEMBER_ME: 'remember_me',
} as const
