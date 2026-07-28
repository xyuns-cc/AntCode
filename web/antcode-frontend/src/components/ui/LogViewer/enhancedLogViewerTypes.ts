import type { RefObject } from 'react'

export const DEFAULT_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const
export const DEFAULT_TYPES = ['stdout', 'stderr'] as const

export type LogMessageType = 'stdout' | 'stderr' | 'info' | 'error' | 'warning' | 'success'
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
  containerRef: RefObject<HTMLDivElement>
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
