import { APP_TITLE, APP_BRAND_NAME, PLATFORM_TITLE } from '@/config/app'
import { RUNTIME_CONFIG } from '@/config/runtime'

// API 相关常量
export const API_BASE_URL = RUNTIME_CONFIG.API_BASE_URL
export const WS_BASE_URL = RUNTIME_CONFIG.WS_BASE_URL

// 应用配置
export { APP_TITLE, APP_BRAND_NAME, PLATFORM_TITLE }
export const APP_VERSION = RUNTIME_CONFIG.APP_VERSION

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
  COMPLETED: 'completed',
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
} as const
