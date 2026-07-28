import { BaseService } from './base'
import apiClient from './api'
import { createLogStreamConnection } from './logStreamConnection'

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
  sequence?: number
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

export type HistoricalLogsPhase = 'loading' | 'loaded' | 'empty' | 'gap'

export interface HistoricalLogsUpdate {
  phase: HistoricalLogsPhase
  sentLines?: number
  truncated?: boolean
}

export interface LogStreamOptions {
  runId: string
  onMessage?: (log: LogEntry) => void
  onError?: (error: unknown) => void
  onStateChange?: (state: string) => void
  onStatusUpdate?: (status: { status: string; message?: string; progress?: number }) => void
  onHistoricalLogsUpdate?: (update: HistoricalLogsUpdate) => void
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
  async getStreamTicket(runId: string): Promise<string> {
    const response = await apiClient.post<{ data?: { ticket?: string }; ticket?: string }>(
      '/api/v1/logs/stream-ticket',
      undefined,
      { params: { run_id: runId } },
    )
    const body = response.data
    const ticket = body?.data?.ticket ?? body?.ticket
    if (!ticket) {
      throw new Error('日志流 ticket 接口未返回 ticket')
    }
    return ticket
  }

  connectLogStream(options: LogStreamOptions): LogStreamConnection | null {
    if (!options.runId) {
      options.onError?.('runId is required for log stream connection')
      return null
    }
    return createLogStreamConnection(() => this.getStreamTicket(options.runId), options)
  }
}

export const logService = new LogService()
export default logService
