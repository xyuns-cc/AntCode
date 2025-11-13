import apiClient from './api'
import Logger from '@/utils/logger'

// 日志条目接口 - 匹配后端API
export interface LogEntry {
  id?: number
  timestamp: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  log_type: 'stdout' | 'stderr' | 'system' | 'application'
  execution_id?: string
  task_id?: number
  message: string
  source?: string
  file_path?: string
  line_number?: number
  extra_data?: Record<string, any>
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

// 新的统一日志响应接口
export interface UnifiedLogResponse {
  execution_id: string
  format: 'structured' | 'raw'
  log_type?: string
  
  // 原始格式字段
  raw_content?: string
  file_path?: string
  file_size?: number
  lines_count?: number
  last_modified?: string
  
  // 结构化格式字段
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

// 日志文件查询参数
export interface LogFileParams {
  execution_id: string
  log_type: 'stdout' | 'stderr'
  lines?: number // 1-10000
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
  task_id?: number
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

// 兼容旧接口的日志响应
export interface LogResponse {
  items: LogEntry[]
  total: number
  page: number
  size: number
}

class LogService {
  // 新的统一日志接口
  async getUnifiedLogs(params: UnifiedLogParams): Promise<UnifiedLogResponse> {
    try {
      const queryParams: any = {
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
      // 返回空内容作为后备
      return {
        execution_id: params.execution_id,
        format: params.format || 'structured',
        log_type: params.log_type,
        structured_data: {
          total: 0,
          page: 1,
          size: 10,
          items: []
        }
      }
    }
  }

  // 获取执行日志文件内容（保持向后兼容）
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
      // 返回空内容作为后备
      return {
        success: false,
        code: 500,
        message: error instanceof Error ? error.message : '获取日志文件失败',
        data: {
          execution_id: executionId,
          log_type: logType,
          content: '',
          file_path: '',
          file_size: 0,
          lines_count: 0
        }
      }
    }
  }

  // 获取执行日志条目（使用新的统一接口）
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
        data: unifiedResponse.structured_data || {
          total: 0,
          page: 1,
          size: 10,
          items: []
        }
      }
    } catch (error) {
      Logger.error('获取执行日志条目失败:', error)
      // 返回空数据作为后备
      return {
        success: false,
        code: 500,
        message: error instanceof Error ? error.message : '获取执行日志条目失败',
        data: {
          total: 0,
          page: 1,
          size: 10,
          items: []
        }
      }
    }
  }

  // 兼容旧接口的获取执行日志方法
  async getExecutionLogsCompat(executionId: string, params?: LogQueryParams): Promise<LogResponse> {
    try {
      const response = await this.getExecutionLogs(executionId, params)
      if (response.success) {
        return {
          items: response.data.items,
          total: response.data.total,
          page: response.data.page,
          size: response.data.size
        }
      }
      throw new Error(response.message)
    } catch (error) {
      // 返回模拟数据作为后备
      return this.getMockLogs({ ...params, execution_id: executionId })
    }
  }

  // 获取日志统计
  async getLogMetrics(): Promise<any> {
    try {
      const response = await apiClient.get('/api/v1/logs/metrics')
      if (response.data.success) {
        return response.data.data
      }
      throw new Error('API响应格式错误')
    } catch (error) {
      return {
        total_log_files: 0,
        total_size_bytes: 0,
        log_levels_count: { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0, CRITICAL: 0 },
        log_types_count: { stdout: 0, stderr: 0 },
        daily_log_count: {}
      }
    }
  }

  // 获取所有日志（使用日志统计和执行日志的组合）
  async getAllLogs(params?: LogQueryParams): Promise<LogResponse> {
    try {
      // 如果指定了execution_id，直接获取执行日志
      if (params?.execution_id) {
        return this.getExecutionLogsCompat(params.execution_id, params)
      }

      // 否则返回模拟数据，因为后端没有统一的日志接口
      return this.getMockLogs(params)
    } catch (error) {
      // 返回模拟数据作为后备
      return this.getMockLogs(params)
    }
  }

  // 获取标准输出日志（使用新的便捷接口）
  async getStdoutLogs(executionId: string, lines?: number): Promise<LogFileResponse> {
    try {
      // 使用新的便捷接口
      const params: any = {}
      if (lines) params.lines = Math.min(Math.max(lines, 1), 10000)
      
      const response = await apiClient.get(`/api/v1/logs/executions/${executionId}/stdout`, { params })
      
      if (response.data.success) {
        return response.data
      }
      throw new Error(response.data.message || 'API响应格式错误')
    } catch (error) {
      // 回退到旧的实现
      Logger.warn('新接口失败，回退到旧接口:', error)
      return this.getExecutionLogFile(executionId, 'stdout', lines)
    }
  }

  // 获取标准错误日志（使用新的便捷接口）
  async getStderrLogs(executionId: string, lines?: number): Promise<LogFileResponse> {
    try {
      // 使用新的便捷接口
      const params: any = {}
      if (lines) params.lines = Math.min(Math.max(lines, 1), 10000)
      
      const response = await apiClient.get(`/api/v1/logs/executions/${executionId}/stderr`, { params })
      
      if (response.data.success) {
        return response.data
      }
      throw new Error(response.data.message || 'API响应格式错误')
    } catch (error) {
      // 回退到旧的实现
      Logger.warn('新接口失败，回退到旧接口:', error)
      return this.getExecutionLogFile(executionId, 'stderr', lines)
    }
  }

  // 获取指定类型的日志条目
  async getLogsByType(executionId: string, logType: 'stdout' | 'stderr', params?: Omit<LogQueryParams, 'execution_id' | 'log_type'>): Promise<LogListResponse> {
    return this.getExecutionLogs(executionId, {
      ...params,
      log_type: logType
    })
  }

  // 获取错误级别的日志（使用新的便捷接口）
  async getErrorLogs(executionId: string, params?: Omit<LogQueryParams, 'execution_id' | 'level'>): Promise<LogListResponse> {
    try {
      // 使用新的便捷接口
      const queryParams: any = {}
      if (params?.search) queryParams.search = params.search
      if (params?.lines) queryParams.lines = Math.min(Math.max(params.lines, 1), 10000)
      
      const response = await apiClient.get(`/api/v1/logs/executions/${executionId}/errors`, { params: queryParams })
      
      if (response.data.success) {
        return response.data
      }
      throw new Error(response.data.message || 'API响应格式错误')
    } catch (error) {
      // 回退到旧的实现
      Logger.warn('新接口失败，回退到旧接口:', error)
      return this.getExecutionLogs(executionId, {
        ...params,
        level: 'ERROR'
      })
    }
  }

  // WebSocket 功能（优化连接管理）
  connectLogStream(executionId?: string, onMessage?: (log: LogEntry) => void, onError?: (error: any) => void): WebSocket | null {
    if (!executionId) {
      console.error('executionId is required for WebSocket connection')
      onError?.('executionId is required for WebSocket connection')
      return null
    }

    try {
      const token = localStorage.getItem('access_token')
      if (!token) {
        console.error('No access token found')
        onError?.('No access token found')
        return null
      }

      // 使用新的WebSocket端点
      const wsUrl = `ws://localhost:8000/api/v1/ws/executions/${executionId}/logs?token=${encodeURIComponent(token)}`
      const ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        // WebSocket连接已建立
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          
          if (data.type === 'log_line' && data.data) {
            const logEntry: LogEntry = {
              id: Date.now() + Math.random(),
              timestamp: data.data.timestamp || data.timestamp,
              level: (data.data.level || 'INFO') as 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL',
              log_type: (data.data.log_type || 'stdout') as 'stdout' | 'stderr' | 'system' | 'application',
              execution_id: data.data.execution_id || executionId,
              message: data.data.content || data.data.message || '',
              source: data.data.source
            }
            onMessage?.(logEntry)
          } else if (data.type === 'historical_logs' && data.data) {
            // 处理历史日志
            data.data.forEach((logLine: any, index: number) => {
              const logEntry: LogEntry = {
                id: Date.now() + index,
                timestamp: logLine.timestamp,
                level: (logLine.level || 'INFO') as 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL',
                log_type: (logLine.log_type || 'stdout') as 'stdout' | 'stderr' | 'system' | 'application',
                execution_id: logLine.execution_id || executionId,
                message: logLine.content || logLine.message || '',
                source: logLine.source
              }
              onMessage?.(logEntry)
            })
          } else if (data.type === 'error') {
            console.error('WebSocket服务端错误:', data.message || data.data)
            onError?.(data.message || data.data || 'WebSocket服务端错误')
          }
        } catch (error) {
          console.error('解析WebSocket消息失败:', error)
          onError?.(error)
        }
      }

      ws.onerror = (error) => {
        console.error('WebSocket连接错误:', error)
        onError?.(error)
      }

      ws.onclose = (event) => {
        // 只有在异常关闭时才报告错误
        if (event.code !== 1000 && event.code !== 1001) {
          onError?.(`WebSocket连接意外关闭: ${event.code} - ${event.reason}`)
        }
      }

      return ws
    } catch (error) {
      console.error('创建WebSocket连接失败:', error)
      onError?.(error)
      return null
    }
  }

  // 模拟日志数据（作为后备）
  private getMockLogs(params?: LogQueryParams): LogResponse {
    const mockLogs: LogEntry[] = [
      {
        id: 1,
        timestamp: new Date().toISOString(),
        level: 'INFO',
        log_type: 'stdout',
        source: 'system',
        message: '系统启动完成',
        extra_data: { version: '1.3.0' }
      },
      {
        id: 2,
        timestamp: new Date(Date.now() - 60000).toISOString(),
        level: 'INFO',
        log_type: 'stdout',
        source: 'scheduler',
        message: '任务调度器已启动',
        extra_data: { active_tasks: 5 }
      },
      {
        id: 3,
        timestamp: new Date(Date.now() - 120000).toISOString(),
        level: 'WARNING',
        log_type: 'stderr',
        source: 'task-executor',
        message: '任务执行超时警告',
        task_id: 1,
        execution_id: 'exec-001'
      },
      {
        id: 4,
        timestamp: new Date(Date.now() - 180000).toISOString(),
        level: 'ERROR',
        log_type: 'stderr',
        source: 'database',
        message: '数据库连接失败',
        extra_data: { error: 'Connection timeout' }
      },
      {
        id: 5,
        timestamp: new Date(Date.now() - 240000).toISOString(),
        level: 'DEBUG',
        log_type: 'stdout',
        source: 'api',
        message: 'API请求处理完成',
        extra_data: { method: 'GET', path: '/api/v1/tasks', duration: 45 }
      },
      {
        id: 6,
        timestamp: new Date(Date.now() - 300000).toISOString(),
        level: 'INFO',
        log_type: 'stdout',
        source: 'redis',
        message: 'Redis连接成功',
        extra_data: { version: '7.4.2', memory: '1.34M' }
      }
    ]

    // 应用过滤器
    let filteredLogs = mockLogs

    if (params?.level) {
      filteredLogs = filteredLogs.filter(log => log.level === params.level)
    }

    if (params?.log_type) {
      filteredLogs = filteredLogs.filter(log => log.log_type === params.log_type)
    }

    if (params?.source) {
      filteredLogs = filteredLogs.filter(log => log.source === params.source)
    }

    if (params?.search) {
      const search = params.search.toLowerCase()
      filteredLogs = filteredLogs.filter(log =>
        log.message.toLowerCase().includes(search) ||
        (log.source && log.source.toLowerCase().includes(search))
      )
    }

    if (params?.task_id) {
      filteredLogs = filteredLogs.filter(log => log.task_id === params.task_id)
    }

    if (params?.execution_id) {
      filteredLogs = filteredLogs.filter(log => log.execution_id === params.execution_id)
    }

    if (params?.execution_id) {
      filteredLogs = filteredLogs.filter(log => log.execution_id === params.execution_id)
    }

    // 分页
    const page = params?.page || 1
    const size = params?.size || 50
    const start = (page - 1) * size
    const end = start + size
    const paginatedLogs = filteredLogs.slice(start, end)

    return {
      items: paginatedLogs,
      total: filteredLogs.length,
      page,
      size
    }
  }

  // 导出日志
  async exportLogs(params?: LogQueryParams, format: 'json' | 'csv' = 'json'): Promise<Blob> {
    try {
      // 由于后端没有导出接口，直接获取数据并生成文件
      const logs = await this.getAllLogs(params)
      const content = format === 'json'
        ? JSON.stringify(logs.items, null, 2)
        : this.convertToCSV(logs.items)

      return new Blob([content], {
        type: format === 'json' ? 'application/json' : 'text/csv'
      })
    } catch (error) {
      // 创建空的导出数据
      const content = format === 'json' ? '[]' : 'timestamp,level,source,message\n'
      return new Blob([content], {
        type: format === 'json' ? 'application/json' : 'text/csv'
      })
    }
  }

  // 转换为CSV格式
  private convertToCSV(logs: LogEntry[]): string {
    const headers = ['timestamp', 'level', 'source', 'message', 'task_id', 'execution_id']
    const csvContent = [
      headers.join(','),
      ...logs.map(log => [
        log.timestamp,
        log.level,
        log.source,
        `"${log.message.replace(/"/g, '""')}"`,
        log.task_id || '',
        log.execution_id || ''
      ].join(','))
    ].join('\n')
    
    return csvContent
  }

  // 清空日志
  async clearLogs(source?: string): Promise<void> {
    try {
      // 后端没有清空日志的接口，这里只是模拟
      // 可以在这里调用后端的清空接口，如果有的话
      // await apiClient.delete('/api/v1/logs', {
      //   params: source ? { source } : undefined
      // })
    } catch (error) {
      throw error
    }
  }
}

export const logService = new LogService()
export default logService
