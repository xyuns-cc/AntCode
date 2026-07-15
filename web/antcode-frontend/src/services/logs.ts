import { BaseService } from './base'
import apiClient from './api'
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
   * 申请日志流一次性 ticket（避免把长期 JWT 写入 URL）。
   * 原生 EventSource 无法携带 Authorization 头，故用 60s TTL 的一次性票据换取接入；
   * 后端 401 时由 apiClient 响应拦截器统一处理（刷新 token + 重试）。
   */
  async getStreamTicket(): Promise<string> {
    const response = await apiClient.post<{ data?: { ticket?: string }; ticket?: string }>(
      '/api/v1/logs/stream-ticket'
    )
    const body = response.data
    const ticket = body?.data?.ticket ?? body?.ticket
    if (!ticket) {
      throw new Error('日志流 ticket 接口未返回 ticket')
    }
    return ticket
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
      onError?.('runId is required for log stream connection')
      return null
    }

    let source: EventSource | null = null
    let reconnectAttempts = 0
    const maxReconnectAttempts = 5
    let manualClose = false
    // 探活 watchdog：服务端每 15s 发 ping 事件，45s 无任何事件视为死连接
    const WATCHDOG_STALE_MS = 45_000
    const WATCHDOG_CHECK_MS = 15_000
    let lastEventAt = Date.now()
    let watchdog: ReturnType<typeof setInterval> | null = null

    const stopWatchdog = () => {
      if (watchdog) {
        clearInterval(watchdog)
        watchdog = null
      }
    }

    const teardown = () => {
      stopWatchdog()
      source?.close()
      source = null
    }

    const scheduleReconnect = () => {
      if (manualClose) return
      if (reconnectAttempts >= maxReconnectAttempts) {
        onStateChange?.('failed')
        onError?.('日志流连接失败：已达最大重连次数')
        return
      }
      reconnectAttempts += 1
      const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts - 1), 30000)
      onStateChange?.('reconnecting')
      setTimeout(() => {
        if (manualClose) return
        void connect()
      }, delay)
    }

    const parseData = <T = Record<string, unknown>>(event: MessageEvent): T | null => {
      try {
        return JSON.parse(event.data) as T
      } catch (e) {
        Logger.error('解析日志 SSE 消息失败:', e)
        return null
      }
    }

    const connect = async () => {
      try {
        // ticket 为一次性消费，每次（重）连接都必须先换新 ticket
        const ticket = await this.getStreamTicket()
        const url = `/api/v1/logs/runs/${runId}/stream?ticket=${encodeURIComponent(ticket)}`
        const es = new EventSource(url)
        source = es
        lastEventAt = Date.now()

        const touch = () => {
          lastEventAt = Date.now()
        }

        es.onopen = () => {
          reconnectAttempts = 0
          touch()
          onStateChange?.('connected')
        }

        es.addEventListener('log_line', (event) => {
          touch()
          const message = parseData<{
            timestamp?: string
            data?: {
              timestamp?: string
              level?: string
              log_type?: string
              run_id?: string
              content?: string
              message?: string
              source?: string
              sequence?: number | null
            }
          }>(event)
          const data = message?.data
          if (!data) return

          const logEntry: LogEntry = {
            id: `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`,
            timestamp: data.timestamp || message?.timestamp || new Date().toISOString(),
            level: (data.level || 'INFO') as LogLevel,
            log_type: (data.log_type || 'stdout') as LogType,
            run_id: data.run_id || runId,
            message: data.content || data.message || '',
            source: data.source,
          }
          onMessage?.(logEntry)
        })

        es.addEventListener('run_status', (event) => {
          touch()
          const message = parseData<{
            data?: { status?: string; message?: string; progress?: number }
          }>(event)
          if (!message?.data?.status) return
          onStatusUpdate?.({
            status: message.data.status,
            message: message.data.message,
            progress: message.data.progress,
          })
        })

        es.addEventListener('historical_logs_start', (event) => {
          touch()
          void event
          onHistoricalLogsUpdate?.({ phase: 'loading' })
        })

        es.addEventListener('historical_logs_end', (event) => {
          touch()
          const message = parseData<{ sent_lines?: number }>(event)
          const sentLines = Number(message?.sent_lines)
          onHistoricalLogsUpdate?.({
            phase: 'loaded',
            sentLines: Number.isFinite(sentLines) ? sentLines : 0,
          })
        })

        es.addEventListener('no_historical_logs', (event) => {
          touch()
          void event
          onHistoricalLogsUpdate?.({ phase: 'empty', sentLines: 0 })
        })

        es.addEventListener('ping', () => {
          // 仅用于保活与 watchdog 刷新，SSE 单向推送无需回 pong
          touch()
        })

        // 服务端业务错误（会话失效 / 慢消费者 / 超过最大寿命），与网络层 onerror 区分
        es.addEventListener('stream_error', (event) => {
          touch()
          const message = parseData<{ message?: string }>(event)
          onError?.(message?.message || '日志流服务端错误')
          // 服务端随后会结束流，触发 onerror 走统一重连
        })

        es.onerror = () => {
          // 关键：ticket 是一次性的，绝不能让 EventSource 用旧 URL 自动重连
          //（自动重连必然 401 且无限打后端），统一 close 后自管退避重连
          teardown()
          onStateChange?.('disconnected')
          scheduleReconnect()
        }

        stopWatchdog()
        watchdog = setInterval(() => {
          if (manualClose) {
            stopWatchdog()
            return
          }
          if (Date.now() - lastEventAt > WATCHDOG_STALE_MS) {
            Logger.warn('日志流超过 45s 无事件，判定为死连接，重连')
            teardown()
            onStateChange?.('disconnected')
            scheduleReconnect()
          }
        }, WATCHDOG_CHECK_MS)
      } catch (error) {
        // ticket 申请失败等
        onStateChange?.('error')
        onError?.(error)
        scheduleReconnect()
      }
    }

    void connect()

    return {
      disconnect: () => {
        manualClose = true
        teardown()
      },
    }
  }
}

export const logService = new LogService()
export default logService
