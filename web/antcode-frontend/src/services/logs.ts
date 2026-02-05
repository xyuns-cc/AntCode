import apiClient from './api'
import { STORAGE_KEYS } from '@/utils/constants'
import Logger from '@/utils/logger'

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
export type LogType = 'stdout' | 'stderr' | 'system' | 'application'
export type LogFormat = 'structured' | 'raw'

export interface LogEntry {
  id?: string
  timestamp: string
  level: LogLevel
  log_type: LogType
  execution_id?: string
  task_id?: string
  message: string
  source?: string
  file_path?: string
  line_number?: number
  extra_data?: Record<string, unknown>
}

export interface StructuredLogData {
  total: number
  page: number
  size: number
  items: LogEntry[]
}

export interface UnifiedLogResponse {
  execution_id: string
  format: LogFormat
  log_type?: string
  raw_content?: string
  file_path?: string
  file_size?: number
  lines_count?: number
  last_modified?: string
  structured_data?: StructuredLogData
}

export interface UnifiedLogParams {
  execution_id: string
  format?: LogFormat
  log_type?: 'stdout' | 'stderr'
  level?: LogLevel
  lines?: number
  search?: string
}

export interface LogQueryParams {
  execution_id?: string
  log_type?: 'stdout' | 'stderr'
  level?: LogLevel
  lines?: number
  search?: string
  page?: number
  size?: number
  start_time?: string
  end_time?: string
  task_id?: string
}

export interface LogFileResponse {
  success: boolean
  code: number
  message: string
  data: {
    execution_id: string
    log_type: string
    content: string
    file_path: string
    file_size: number
    lines_count: number
    last_modified?: string
  }
}

export interface LogListResponse {
  success: boolean
  code: number
  message: string
  data: StructuredLogData
}

export interface LogStreamConnection {
  disconnect: () => void
}

class LogService {
  async getUnifiedLogs(params: UnifiedLogParams): Promise<UnifiedLogResponse> {
    const queryParams: Record<string, string | number> = {
      format: params.format || 'structured'
    }

    if (params.log_type) queryParams.log_type = params.log_type
    if (params.level) queryParams.level = params.level
    if (params.lines) queryParams.lines = Math.min(Math.max(params.lines, 1), 10000)
    if (params.search) queryParams.search = params.search

    const response = await apiClient.get(`/api/v1/logs/executions/${params.execution_id}`, { params: queryParams })
    return response.data.data as UnifiedLogResponse
  }

  async getExecutionLogs(executionId: string, params?: LogQueryParams): Promise<LogListResponse> {
    const unified = await this.getUnifiedLogs({
      execution_id: executionId,
      format: 'structured',
      log_type: params?.log_type,
      level: params?.level,
      lines: params?.lines,
      search: params?.search,
    })

    if (!unified.structured_data) {
      throw new Error('日志接口返回缺少 structured_data')
    }

    return {
      success: true,
      code: 200,
      message: '获取成功',
      data: unified.structured_data,
    }
  }

  async getStdoutLogs(executionId: string, lines?: number): Promise<LogFileResponse> {
    const params: Record<string, number> = {}
    if (lines) params.lines = Math.min(Math.max(lines, 1), 10000)

    const response = await apiClient.get(`/api/v1/logs/executions/${executionId}/stdout`, { params })
    const unified = response.data.data as UnifiedLogResponse

    return {
      success: true,
      code: 200,
      message: '获取成功',
      data: {
        execution_id: unified.execution_id,
        log_type: unified.log_type || 'stdout',
        content: unified.raw_content || '',
        file_path: unified.file_path || '',
        file_size: unified.file_size || 0,
        lines_count: unified.lines_count || 0,
        last_modified: unified.last_modified,
      },
    }
  }

  async getStderrLogs(executionId: string, lines?: number): Promise<LogFileResponse> {
    const params: Record<string, number> = {}
    if (lines) params.lines = Math.min(Math.max(lines, 1), 10000)

    const response = await apiClient.get(`/api/v1/logs/executions/${executionId}/stderr`, { params })
    const unified = response.data.data as UnifiedLogResponse

    return {
      success: true,
      code: 200,
      message: '获取成功',
      data: {
        execution_id: unified.execution_id,
        log_type: unified.log_type || 'stderr',
        content: unified.raw_content || '',
        file_path: unified.file_path || '',
        file_size: unified.file_size || 0,
        lines_count: unified.lines_count || 0,
        last_modified: unified.last_modified,
      },
    }
  }

  connectLogStream(
    executionId?: string,
    onMessage?: (log: LogEntry) => void,
    onError?: (error: unknown) => void,
    onStateChange?: (state: string) => void,
    onStatusUpdate?: (status: { status: string; message?: string; progress?: number }) => void
  ): LogStreamConnection | null {
    if (!executionId) {
      onError?.('executionId is required for WebSocket connection')
      return null
    }

    const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
    if (!token) {
      onError?.('No access token found')
      return null
    }

    const wsHost = import.meta.env.VITE_WS_HOST || window.location.host
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${wsProtocol}//${wsHost}/api/v1/ws/executions/${executionId}/logs?token=${encodeURIComponent(token)}`

    let ws: WebSocket | null = null
    let reconnectAttempts = 0
    const maxReconnectAttempts = 5
    let manualClose = false

    const connect = () => {
      try {
        ws = new WebSocket(wsUrl)

        ws.onopen = () => {
          reconnectAttempts = 0
          onStateChange?.('connected')
        }

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data)

            if (message.type === 'log_line' && message.data) {
              const logEntry: LogEntry = {
                id: `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,
                timestamp: message.data.timestamp || message.timestamp,
                level: (message.data.level || 'INFO') as LogLevel,
                log_type: (message.data.log_type || 'stdout') as LogType,
                execution_id: message.data.execution_id || executionId,
                message: message.data.content || message.data.message || '',
                source: message.data.source,
              }
              onMessage?.(logEntry)
              return
            }

            if (message.type === 'ping') {
              ws?.send(JSON.stringify({ type: 'pong', timestamp: new Date().toISOString() }))
              return
            }

            if (message.type === 'execution_status' && message.data) {
              onStatusUpdate?.({
                status: message.data.status,
                message: message.data.message,
                progress: message.data.progress,
              })
              return
            }

            if (message.type === 'error') {
              onError?.(message.message || 'WebSocket server error')
            }
          } catch (e) {
            Logger.error('解析日志 WebSocket 消息失败:', e)
          }
        }

        ws.onerror = (error) => {
          onStateChange?.('error')
          onError?.(error)
        }

        ws.onclose = (event) => {
          onStateChange?.('disconnected')

          if (!manualClose && reconnectAttempts < maxReconnectAttempts) {
            reconnectAttempts += 1
            const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts - 1), 30000)
            onStateChange?.('reconnecting')
            setTimeout(connect, delay)
            return
          }

          if (!manualClose && reconnectAttempts >= maxReconnectAttempts) {
            onStateChange?.('failed')
            onError?.(`WebSocket closed: ${event.code} - ${event.reason}`)
          }
        }
      } catch (error) {
        onError?.(error)
      }
    }

    connect()

    return {
      disconnect: () => {
        manualClose = true
        ws?.close(1000, '客户端主动断开')
      },
    }
  }
}

export const logService = new LogService()
export default logService

