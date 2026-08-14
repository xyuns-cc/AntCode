import type { RefObject } from 'react'
import type { LogType } from '@/services/logs'

export const DEFAULT_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const

/**
 * 后端 LogType 的展示名。类型写成 `Record<LogType, string>`，后端
 * （antcode_core.domain.schemas.logs.LogType）新增取值时这里漏了就是编译错误。
 * 之前少了 system / application：DEFAULT_TYPES 只列两项，用户一旦收窄类型过滤器，
 * filterLogMessages 就会把这两类日志整行丢掉，且过滤器里根本没有能把它们选回来的选项。
 */
export const LOG_TYPE_LABELS: Record<LogType, string> = {
  stdout: '标准输出',
  stderr: '标准错误',
  system: '系统',
  application: '应用',
}

export const DEFAULT_TYPES = Object.keys(LOG_TYPE_LABELS) as LogType[]

/** 后端日志类型 + 本地生成的通知类型（createNotice）。 */
export type LogMessageType = LogType | 'info' | 'error' | 'warning' | 'success'
export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error'
export type HistoricalStatus = 'idle' | 'loading' | 'loaded' | 'empty'
export type ExportFormat = 'txt' | 'json' | 'csv'

export interface LogMessage {
  id: string
  type: LogMessageType
  content: string
  timestamp: string
  level?: string
  source?: string
  sequence?: number
  sequenceIdentity?: string
}

export interface ExecutionStatusUpdate {
  status: string
  message?: string
  progress?: number
}

export interface EnhancedLogViewerProps {
  runId: string
  height?: number
  showControls?: boolean
  autoConnect?: boolean
  showStdout?: boolean
  showStderr?: boolean
  maxLines?: number
  enableSearch?: boolean
  enableExport?: boolean
  enableVirtualization?: boolean
  onLogUpdate?: (logs: string[]) => void
  onStatusUpdate?: (status: ExecutionStatusUpdate) => void
}

export interface ViewerTheme {
  colorBgContainer: string
  colorBorder: string
  colorBorderSecondary: string
  colorError: string
  colorFillAlter: string
  colorFillQuaternary: string
  colorPrimary: string
  colorSuccess: string
  colorText: string
  colorTextSecondary: string
  colorTextTertiary: string
  colorWarning: string
}

export interface LogStats {
  total: number
  stdout: number
  stderr: number
  errors: number
  warnings: number
}

export interface FilterState {
  searchText: string
  selectedLevels: string[]
  selectedTypes: string[]
}

export interface LogContentProps {
  connectionStatus: ConnectionStatus
  containerRef: RefObject<HTMLDivElement | null>
  enableVirtualization: boolean
  height: number
  messages: LogMessage[]
  allMessageCount: number
  maxLines: number
  autoScroll: boolean
  onAutoScrollChange: (autoScroll: boolean) => void
  onClear: () => void
  searchText: string
  token: ViewerTheme
}
