import apiClient from './api'
import Logger from '@/utils/logger'

// 日志条目接口 - 匹配后端API
export interface LogEntry {
  id?: string  // public_id
  timestamp: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  log_type: 'stdout' | 'stderr' | 'system' | 'application'
  execution_id?: string
  task_id?: string  // public_id
  message: string
  source?: string
  file_path?: string
  line_number?: number
  extra_data?: Record<string, unknown>
}

// 日志文件内容响应
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

// 统一日志响应接口
export interface UnifiedLogResponse {
  execution_id: string
  format: 'structured' | 'raw'
  log_type?: string
  raw_content?: string
  file_path?: string
  file_size?: number
  lines_count?: number
  last_modified?: string
  structured_data?: {
    total: number
    page: number
    size: number
    items: LogEntry[]
  }
}

// 统一日志请求参数
export interface UnifiedLogParams {
  execution_id: string
  format?: 'structured' | 'raw'
  log_type?: 'stdout' | 'stderr'
  level?: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  lines?: number
  search?: string
}

// 日志条目查询参数
export interface LogQueryParams {
  execution_id?: string
  log_type?: 'stdout' | 'stderr'
  level?: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  lines?: number
  search?: string
  page?: number
  size?: number
  start_time?: string
  end_time?: string
  task_id?: string
  source?: string
}

// 日志列表响应
export interface LogListResponse {
  success: boolean
  code: number
  message: string
  data: {
    total: number
    page: number
    size: number
    items: LogEntry[]
  }
}

// 日志响应
export interface LogResponse {
  items: LogEntry[]
  total: number
  page: number
  size: number
}

export interface LogMetrics {
  total_log_files: number
  total_size_bytes: number
  log_levels_count: Record<string, number>
  log_types_count: Record<string, number>
  daily_log_count: Record<string, number>
}

class LogService {
  // 统一日志接口
  async getUnifiedLogs(params: UnifiedLogParams): Promise<UnifiedLogResponse> {
    try {
      const queryParams: Record<string, string | number> = {
        format: params.format || 'structured'
      }
      if (params.log_type) queryParams.log_type = params.log_type
      if (params.level) queryParams.level = params.level
      if (params.lines) queryParams.lines = Math.min(Math.max(params.lines, 1), 10000)
      if (params.search) queryParams.search = params.search

      const response = await apiClient.get(`/api/v1/logs/executions/${params.execution_id}`, { params: queryParams })
      if (response.data.success) {
        return response.data.data
      }
      throw new Error(response.data.message || 'API响应格式错误')
    } catch (error) {
      Logger.error('获取统一日志失败:', error)
      return {
        execution_id: params.execution_id,
        format: params.format || 'structured',
        log_type: params.log_type,
        structured_data: { total: 0, page: 1, size: 10, items: [] }
      }
    }
  }

  // 获取执行日志文件内容
  async getExecutionLogFile(executionId: string, logType: 'stdout' | 'stderr', lines?: number): Promise<LogFileResponse> {
    try {
      const unifiedResponse = await this.getUnifiedLogs({
        execution_id: executionId,
        format: 'raw',
        log_type: logType,
        lines
      })
      return {
        success: true,
        code: 200,
        message: '获取成功',
        data: {
          execution_id: unifiedResponse.execution_id,
          log_type: unifiedResponse.log_type || logType,
          content: unifiedResponse.raw_content || '',
          file_path: unifiedResponse.file_path || '',
          file_size: unifiedResponse.file_size || 0,
          lines_count: unifiedResponse.lines_count || 0,
          last_modified: unifiedResponse.last_modified
        }
      }
    } catch (error) {
      Logger.error('获取日志文件失败:', error)
      return {
        success: false,
        code: 500,
        message: error instanceof Error ? error.message : '获取日志文件失败',
        data: { execution_id: executionId, log_type: logType, content: '', file_path: '', file_size: 0, lines_count: 0 }
      }
    }
  }

  // 获取执行日志条目
  async getExecutionLogs(executionId: string, params?: LogQueryParams): Promise<LogListResponse> {
    try {
      const unifiedResponse = await this.getUnifiedLogs({
        execution_id: executionId,
        format: 'structured',
        log_type: params?.log_type,
        level: params?.level,
        lines: params?.lines,
        search: params?.search
      })
      return {
        success: true,
        code: 200,
        message: '获取成功',
        data: unifiedResponse.structured_data || { total: 0, page: 1, size: 10, items: [] }
      }
    } catch (error) {
      Logger.error('获取执行日志条目失败:', error)
      return {
        success: false,
        code: 500,
        message: error instanceof Error ? error.message : '获取执行日志条目失败',
        data: { total: 0, page: 1, size: 10, items: [] }
      }
    }
  }

  // 获取执行日志（返回简化格式）
  async getExecutionLogsCompat(executionId: string, params?: LogQueryParams): Promise<LogResponse> {
    try {
      const response = await this.getExecutionLogs(executionId, params)
      if (response.success) {
        return { items: response.data.items, total: response.data.total, page: response.data.page, size: response.data.size }
      }
      throw new Error(response.message)
    } catch {
      return this.getMockLogs({ ...params, execution_id: executionId })
    }
  }

  // 获取日志统计
  async getLogMetrics(): Promise<LogMetrics> {
    try {
      const response = await apiClient.get<{ success: boolean; data?: LogMetrics }>('/api/v1/logs/metrics')
      if (response.data.success && response.data.data) {
        return response.data.data
      }
      throw new Error('API响应格式错误')
    } catch (error) {
      Logger.warn('获取日志统计失败，返回默认值', error)
      return {
        total_log_files: 0,
        total_size_bytes: 0,
        log_levels_count: { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0, CRITICAL: 0 },
        log_types_count: { stdout: 0, stderr: 0 },
        daily_log_count: {}
      }
    }
  }

  // 获取所有日志
  async getAllLogs(params?: LogQueryParams): Promise<LogResponse> {
    try {
      if (params?.execution_id) {
        return this.getExecutionLogsCompat(params.execution_id, params)
      }
      return this.getMockLogs(params)
    } catch {
      return this.getMockLogs(params)
    }
  }

  // 获取标准输出日志
  async getStdoutLogs(executionId: string, lines?: number): Promise<LogFileResponse> {
    try {
      const params: Record<string, number> = {}
      if (lines) params.lines = Math.min(Math.max(lines, 1), 10000)
      const response = await apiClient.get(`/api/v1/logs/executions/${executionId}/stdout`, { params })
      if (response.data.success) {
        return response.data
      }
      throw new Error(response.data.message || 'API响应格式错误')
    } catch (error) {
      Logger.warn('新接口失败，回退:', error)
      return this.getExecutionLogFile(executionId, 'stdout', lines)
    }
  }

  // 获取标准错误日志
  async getStderrLogs(executionId: string, lines?: number): Promise<LogFileResponse> {
    try {
      const params: Record<string, number> = {}
      if (lines) params.lines = Math.min(Math.max(lines, 1), 10000)
      const response = await apiClient.get(`/api/v1/logs/executions/${executionId}/stderr`, { params })
      if (response.data.success) {
        return response.data
      }
      throw new Error(response.data.message || 'API响应格式错误')
    } catch (error) {
      Logger.warn('新接口失败，回退:', error)
      return this.getExecutionLogFile(executionId, 'stderr', lines)
    }
  }

  // 获取指定类型的日志条目
  async getLogsByType(executionId: string, logType: 'stdout' | 'stderr', params?: Omit<LogQueryParams, 'execution_id' | 'log_type'>): Promise<LogListResponse> {
    return this.getExecutionLogs(executionId, { ...params, log_type: logType })
  }

  // 获取错误级别的日志
  async getErrorLogs(executionId: string, params?: Omit<LogQueryParams, 'execution_id' | 'level'>): Promise<LogListResponse> {
    try {
      const queryParams: Record<string, string | number> = {}
      if (params?.search) queryParams.search = params.search
      if (params?.lines) queryParams.lines = Math.min(Math.max(params.lines, 1), 10000)
      const response = await apiClient.get(`/api/v1/logs/executions/${executionId}/errors`, { params: queryParams })
      if (response.data.success) {
        return response.data
      }
      throw new Error(response.data.message || 'API响应格式错误')
    } catch (error) {
      Logger.warn('新接口失败，回退:', error)
      return this.getExecutionLogs(executionId, { ...params, level: 'ERROR' })
    }
  }


  // WebSocket 日志流连接
  connectLogStream(
    executionId?: string, 
    onMessage?: (log: LogEntry) => void, 
    onError?: (error: unknown) => void,
    onStateChange?: (state: string) => void,
    onStatusUpdate?: (status: { status: string; message?: string; progress?: number }) => void
  ): { disconnect: () => void } | null {
    if (!executionId) {
      Logger.error('executionId is required for WebSocket connection')
      onError?.('executionId is required for WebSocket connection')
      return null
    }

    const token = localStorage.getItem('access_token')
    if (!token) {
      Logger.error('No access token found')
      onError?.('No access token found')
      return null
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = import.meta.env.VITE_WS_HOST || 'localhost:8000'
    const wsUrl = `${protocol}//${host}/api/v1/ws/executions/${executionId}/logs?token=${encodeURIComponent(token)}`
    
    let ws: WebSocket | null = null
    let reconnectAttempts = 0
    const maxReconnectAttempts = 5
    let manualClose = false

    const connect = () => {
      try {
        ws = new WebSocket(wsUrl)
        
        ws.onopen = () => {
          Logger.info('WebSocket 连接已建立')
          reconnectAttempts = 0
          onStateChange?.('connected')
        }

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data)
            
            if (message.type === 'log_line' && message.data) {
              const logEntry: LogEntry = {
                id: `${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                timestamp: message.data.timestamp || message.timestamp,
                level: (message.data.level || 'INFO') as LogEntry['level'],
                log_type: (message.data.log_type || 'stdout') as LogEntry['log_type'],
                execution_id: message.data.execution_id || executionId,
                message: message.data.content || message.data.message || '',
                source: message.data.source
              }
              onMessage?.(logEntry)
            } else if (message.type === 'ping') {
              ws?.send(JSON.stringify({ type: 'pong', timestamp: new Date().toISOString() }))
            } else if (message.type === 'execution_status' && onStatusUpdate && message.data) {
              onStatusUpdate({ status: message.data.status, message: message.data.message, progress: message.data.progress })
            } else if (message.type === 'error') {
              Logger.error('服务器错误:', message.message)
              onError?.(message.message)
            }
          } catch (e) {
            Logger.error('解析消息失败:', e)
          }
        }

        ws.onerror = (error) => {
          Logger.error('WebSocket 错误:', error)
          onStateChange?.('error')
        }

        ws.onclose = (event) => {
          Logger.info(`WebSocket 关闭: ${event.code} - ${event.reason}`)
          onStateChange?.('disconnected')
          
          if (!manualClose && reconnectAttempts < maxReconnectAttempts) {
            reconnectAttempts++
            const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts - 1), 30000)
            Logger.info(`将在 ${delay}ms 后重连 (${reconnectAttempts}/${maxReconnectAttempts})`)
            onStateChange?.('reconnecting')
            setTimeout(connect, delay)
          } else if (reconnectAttempts >= maxReconnectAttempts) {
            onError?.('达到最大重连次数')
            onStateChange?.('failed')
          }
        }
      } catch (error) {
        Logger.error('创建 WebSocket 失败:', error)
        onError?.(error)
      }
    }

    connect()
    return { disconnect: () => { manualClose = true; ws?.close(1000, '客户端主动断开') } }
  }

  // 模拟日志数据（作为后备）
  private getMockLogs(params?: LogQueryParams): LogResponse {
    const mockLogs: LogEntry[] = [
      { id: '1', timestamp: new Date().toISOString(), level: 'INFO', log_type: 'stdout', source: 'system', message: '系统启动完成' },
      { id: '2', timestamp: new Date(Date.now() - 60000).toISOString(), level: 'INFO', log_type: 'stdout', source: 'scheduler', message: '任务调度器已启动' },
      { id: '3', timestamp: new Date(Date.now() - 120000).toISOString(), level: 'WARNING', log_type: 'stderr', source: 'task-executor', message: '任务执行超时警告', task_id: '1', execution_id: 'exec-001' },
      { id: '4', timestamp: new Date(Date.now() - 180000).toISOString(), level: 'ERROR', log_type: 'stderr', source: 'database', message: '数据库连接失败' },
      { id: '5', timestamp: new Date(Date.now() - 240000).toISOString(), level: 'DEBUG', log_type: 'stdout', source: 'api', message: 'API请求处理完成' },
      { id: '6', timestamp: new Date(Date.now() - 300000).toISOString(), level: 'INFO', log_type: 'stdout', source: 'redis', message: 'Redis连接成功' }
    ]

    let filteredLogs = mockLogs
    if (params?.level) filteredLogs = filteredLogs.filter(log => log.level === params.level)
    if (params?.log_type) filteredLogs = filteredLogs.filter(log => log.log_type === params.log_type)
    if (params?.source) filteredLogs = filteredLogs.filter(log => log.source === params.source)
    if (params?.search) {
      const search = params.search.toLowerCase()
      filteredLogs = filteredLogs.filter(log => log.message.toLowerCase().includes(search) || (log.source && log.source.toLowerCase().includes(search)))
    }
    if (params?.task_id) filteredLogs = filteredLogs.filter(log => log.task_id === params.task_id)
    if (params?.execution_id) filteredLogs = filteredLogs.filter(log => log.execution_id === params.execution_id)

    const page = params?.page || 1
    const size = params?.size || 50
    const start = (page - 1) * size
    const paginatedLogs = filteredLogs.slice(start, start + size)

    return { items: paginatedLogs, total: filteredLogs.length, page, size }
  }

  // 导出日志
  async exportLogs(params?: LogQueryParams, format: 'json' | 'csv' = 'json'): Promise<Blob> {
    try {
      const logs = await this.getAllLogs(params)
      const content = format === 'json' ? JSON.stringify(logs.items, null, 2) : this.convertToCSV(logs.items)
      return new Blob([content], { type: format === 'json' ? 'application/json' : 'text/csv' })
    } catch (error) {
      Logger.warn('导出日志失败，返回空文件', error)
      const content = format === 'json' ? '[]' : 'timestamp,level,source,message\n'
      return new Blob([content], { type: format === 'json' ? 'application/json' : 'text/csv' })
    }
  }

  // 转换为CSV格式
  private convertToCSV(logs: LogEntry[]): string {
    const headers = ['timestamp', 'level', 'source', 'message', 'task_id', 'execution_id']
    return [
      headers.join(','),
      ...logs.map(log => [log.timestamp, log.level, log.source, `"${log.message.replace(/"/g, '""')}"`, log.task_id || '', log.execution_id || ''].join(','))
    ].join('\n')
  }

  // 清空日志
  async clearLogs(_source?: string): Promise<void> {
    // 后端没有清空日志的接口
  }
}

export const logService = new LogService()
export default logService
