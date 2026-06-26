import { BaseService } from './base'
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
  run_id?: string
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
  run_id: string
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
  run_id: string
  format?: LogFormat
  log_type?: 'stdout' | 'stderr'
  level?: LogLevel
  lines?: number
  search?: string
}

export interface LogQueryParams {
  run_id?: string
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
    run_id: string
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

export type HistoricalLogsPhase = 'loading' | 'loaded' | 'empty'

export interface HistoricalLogsUpdate {
  phase: HistoricalLogsPhase
  sentLines?: number
}

class LogService extends BaseService {
  constructor() {
    super('/api/v1')
  }

  async getUnifiedLogs(params: UnifiedLogParams): Promise<UnifiedLogResponse> {
    const queryParams: Record<string, string | number> = {
      format: params.format || 'structured'
    }

    if (params.log_type) queryParams.log_type = params.log_type
    if (params.level) queryParams.level = params.level
    if (params.lines) queryParams.lines = Math.min(Math.max(params.lines, 1), 10000)
    if (params.search) queryParams.search = params.search

    return await this.get<UnifiedLogResponse>(`/logs/runs/${params.run_id}`, { params: queryParams })
  }

  async getRunLogs(runId: string, params?: LogQueryParams): Promise<LogListResponse> {
    const unified = await this.getUnifiedLogs({
      run_id: runId,
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

  async getStdoutLogs(runId: string, lines?: number): Promise<LogFileResponse> {
    const params: Record<string, number> = {}
    if (lines) params.lines = Math.min(Math.max(lines, 1), 10000)

    const unified = await this.get<UnifiedLogResponse>(`/logs/runs/${runId}/stdout`, { params })

    return {
      success: true,
      code: 200,
      message: '获取成功',
      data: {
        run_id: unified.run_id,
        log_type: unified.log_type || 'stdout',
        content: unified.raw_content || '',
        file_path: unified.file_path || '',
        file_size: unified.file_size || 0,
        lines_count: unified.lines_count || 0,
        last_modified: unified.last_modified,
      },
    }
  }

  async getStderrLogs(runId: string, lines?: number): Promise<LogFileResponse> {
    const params: Record<string, number> = {}
    if (lines) params.lines = Math.min(Math.max(lines, 1), 10000)

    const unified = await this.get<UnifiedLogResponse>(`/logs/runs/${runId}/stderr`, { params })

    return {
      success: true,
      code: 200,
      message: '获取成功',
      data: {
        run_id: unified.run_id,
        log_type: unified.log_type || 'stderr',
        content: unified.raw_content || '',
        file_path: unified.file_path || '',
        file_size: unified.file_size || 0,
        lines_count: unified.lines_count || 0,
        last_modified: unified.last_modified,
      },
    }
  }

  /**
   * 申请 WebSocket 一次性 ticket（避免把长期 JWT 写入 URL）。
   * 后端 401 时由 apiClient 响应拦截器统一处理；调用方仅在 ticket 缺失时报错。
   * 如果服务端尚未实现 /api/v1/ws-ticket，则回退到 access_token。
   */
  async getWsTicket(): Promise<string | null> {
    try {
      const response = await apiClient.post<{ data?: { ticket?: string }; ticket?: string }>(
        '/api/v1/ws-ticket'
      )
      const body = response.data
      const ticket = body?.data?.ticket ?? body?.ticket
      if (ticket) return ticket
    } catch (error) {
      Logger.warn('获取 WebSocket ticket 失败，回退到 access_token', error)
    }
    return localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
  }

  connectLogStream(
    runId?: string,
    onMessage?: (log: LogEntry) => void,
    onError?: (error: unknown) => void,
    onStateChange?: (state: string) => void,
    onStatusUpdate?: (status: { status: string; message?: string; progress?: number }) => void,
    onHistoricalLogsUpdate?: (update: HistoricalLogsUpdate) => void
  ): LogStreamConnection | null {
    if (!runId) {
      onError?.('runId is required for WebSocket connection')
      return null
    }

    const wsHost = import.meta.env.VITE_WS_HOST || window.location.host
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'

    let ws: WebSocket | null = null
    let reconnectAttempts = 0
    const maxReconnectAttempts = 5
    let manualClose = false
    let closeCode = 0
    // 历史日志去重（按 timestamp|sequence|log_type|content 前 64 字节）
    const seenKeys = new Set<string>()
    const MAX_SEEN = 50_000
    const SEEN_AGE_HALF = 25_000

    const logKey = (entry: {
      timestamp?: string
      log_type?: string
      sequence?: number
      line_number?: number
      message?: string
    }): string => {
      const seq = entry.sequence ?? entry.line_number ?? ''
      const msg = (entry.message ?? '').slice(0, 64)
      return `${entry.timestamp ?? ''}|${seq}|${entry.log_type ?? ''}|${msg}`
    }

    const buildUrl = async (): Promise<string | null> => {
      // 通过一次性 ticket 鉴权（401 时拦截器会刷新 token + 重试）
      const ticket = await this.getWsTicket()
      if (!ticket) {
        onError?.('No access token / ticket available')
        return null
      }
      return `${wsProtocol}//${wsHost}/api/v1/ws/runs/${runId}/logs?ticket=${encodeURIComponent(ticket)}`
    }

    const connect = async () => {
      try {
        const wsUrl = await buildUrl()
        if (!wsUrl) return

        ws = new WebSocket(wsUrl)

        ws.onopen = () => {
          reconnectAttempts = 0
          onStateChange?.('connected')
        }

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data)

            if (message.type === 'log_line' && message.data) {
              const data = message.data
              const dedupKey = logKey({
                timestamp: data.timestamp || message.timestamp,
                log_type: data.log_type,
                sequence: data.sequence,
                line_number: data.line_number,
                message: data.content || data.message,
              })

              // 重连后历史日志可能与已收到的实时日志重叠，去重防止 UI 重复
              if (seenKeys.has(dedupKey)) return
              seenKeys.add(dedupKey)
              // 老化：超 50k 条仅保留最近 25k
              if (seenKeys.size > MAX_SEEN) {
                const arr = Array.from(seenKeys)
                seenKeys.clear()
                for (const k of arr.slice(arr.length - SEEN_AGE_HALF)) {
                  seenKeys.add(k)
                }
              }

              const logEntry: LogEntry = {
                id: `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,
                timestamp: data.timestamp || message.timestamp,
                level: (data.level || 'INFO') as LogLevel,
                log_type: (data.log_type || 'stdout') as LogType,
                run_id: data.run_id || runId,
                message: data.content || data.message || '',
                source: data.source,
                line_number: data.line_number,
              }
              onMessage?.(logEntry)
              return
            }

            if (message.type === 'ping') {
              ws?.send(JSON.stringify({ type: 'pong', timestamp: new Date().toISOString() }))
              return
            }

            if (message.type === 'run_status' && message.data) {
              onStatusUpdate?.({
                status: message.data.status,
                message: message.data.message,
                progress: message.data.progress,
              })
              return
            }

            if (message.type === 'historical_logs_start') {
              onHistoricalLogsUpdate?.({ phase: 'loading' })
              return
            }

            if (message.type === 'historical_logs_end') {
              const sentLines = Number(message.sent_lines)
              onHistoricalLogsUpdate?.({
                phase: 'loaded',
                sentLines: Number.isFinite(sentLines) ? sentLines : 0,
              })
              return
            }

            if (message.type === 'no_historical_logs') {
              onHistoricalLogsUpdate?.({ phase: 'empty', sentLines: 0 })
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
          closeCode = event.code
          onStateChange?.('disconnected')

          if (manualClose) return

          // 鉴权失败（4401/4403）：尝试刷新 token 再重连一次
          const isAuthClose = closeCode === 4401 || closeCode === 4403

          if (reconnectAttempts < maxReconnectAttempts) {
            reconnectAttempts += 1
            const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts - 1), 30000)
            onStateChange?.('reconnecting')
            setTimeout(() => {
              if (manualClose) return
              if (isAuthClose) {
                // 异步刷新 token：通过任何 API 调用触发拦截器；这里直接重新申请 ticket
                void connect()
              } else {
                void connect()
              }
            }, delay)
            return
          }

          onStateChange?.('failed')
          onError?.(`WebSocket closed: ${event.code} - ${event.reason}`)
        }
      } catch (error) {
        onError?.(error)
      }
    }

    void connect()

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
