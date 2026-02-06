import showNotification from '@/utils/notification'
import Logger from '@/utils/logger'
import { logService } from '@/services/logs'
import type { LogEntry, LogFileResponse } from '@/services/logs'

// 导出格式类型
export type ExportFormat = 'txt' | 'json' | 'csv'

// 导出配置接口
export interface LogExportConfig {
  executionId: string
  format: ExportFormat
  includeStdout?: boolean
  includeStderr?: boolean
  includeTimestamp?: boolean
  includeLevel?: boolean
  includeSource?: boolean
  maxLines?: number
}

// 日志导出工具类
export class LogExporter {
  // 导出日志文件内容
  static async exportLogFile(config: LogExportConfig): Promise<void> {
    const { executionId, format, includeStdout = true, includeStderr = true } = config

    try {
      const promises: Promise<LogFileResponse>[] = []
      
      if (includeStdout) {
        promises.push(logService.getStdoutLogs(executionId, config.maxLines))
      }
      
      if (includeStderr) {
        promises.push(logService.getStderrLogs(executionId, config.maxLines))
      }

      const responses = await Promise.all(promises)
      const logData: Array<{ type: 'stdout' | 'stderr'; content: string; response: LogFileResponse }> = []

      responses.forEach((response, index) => {
        if (response.success && response.data.content) {
          const type = index === 0 && includeStdout ? 'stdout' : 'stderr'
          logData.push({
            type,
            content: response.data.content,
            response
          })
        }
      })

      if (logData.length === 0) {
        showNotification('warning', '没有日志内容可导出')
        return
      }

      let exportContent = ''
      let filename = `logs_${executionId}_${new Date().toISOString().split('T')[0]}`

      switch (format) {
        case 'txt':
          exportContent = this.formatAsTxt(logData, config)
          filename += '.txt'
          break
          
        case 'json':
          exportContent = this.formatAsJson(logData, config)
          filename += '.json'
          break
          
        case 'csv':
          exportContent = this.formatAsCsv(logData, config)
          filename += '.csv'
          break
      }

      this.downloadFile(exportContent, filename)
      showNotification('success', `日志已导出为 ${format.toUpperCase()} 格式`)

    } catch (error) {
      Logger.error('导出日志失败:', error)
      showNotification('error', '导出日志失败: ' + (error instanceof Error ? error.message : String(error)))
    }
  }

  // 导出日志条目
  static async exportLogEntries(executionId: string, format: ExportFormat, config?: Partial<LogExportConfig>): Promise<void> {
    try {
      const response = await logService.getExecutionLogs(executionId, {
        lines: config?.maxLines || 1000
      })

      if (!response.success || response.data.items.length === 0) {
        showNotification('warning', '没有日志条目可导出')
        return
      }

      const entries = response.data.items
      let exportContent = ''
      let filename = `log_entries_${executionId}_${new Date().toISOString().split('T')[0]}`

      switch (format) {
        case 'txt':
          exportContent = this.formatEntriesAsTxt(entries, config)
          filename += '.txt'
          break
          
        case 'json':
          exportContent = JSON.stringify(entries, null, 2)
          filename += '.json'
          break
          
        case 'csv':
          exportContent = this.formatEntriesAsCsv(entries)
          filename += '.csv'
          break
      }

      this.downloadFile(exportContent, filename)
      showNotification('success', `日志条目已导出为 ${format.toUpperCase()} 格式`)

    } catch (error) {
      Logger.error('导出日志条目失败:', error)
      showNotification('error', '导出日志条目失败: ' + (error instanceof Error ? error.message : String(error)))
    }
  }

  // 格式化为TXT格式
  private static formatAsTxt(
    logData: Array<{ type: 'stdout' | 'stderr'; content: string; response: LogFileResponse }>,
    config: LogExportConfig
  ): string {
    const lines: string[] = []
    
    // 添加头部信息
    lines.push(`# 执行日志导出`)
    lines.push(`# 执行ID: ${config.executionId}`)
    lines.push(`# 导出时间: ${new Date().toLocaleString()}`)
    lines.push(`# 格式: TXT`)
    lines.push('')

    logData.forEach(({ type, content, response }) => {
      lines.push(`## ${type.toUpperCase()} 日志`)
      lines.push(`# 文件路径: ${response.data.file_path}`)
      lines.push(`# 文件大小: ${response.data.file_size} 字节`)
      lines.push(`# 行数: ${response.data.lines_count}`)
      lines.push('')
      
      // 处理日志内容，按行分割并添加时间戳（如果需要）
      const contentLines = content.split('\n')
      contentLines.forEach((line) => {
        if (line.trim()) {
          let formattedLine = line
          if (config.includeTimestamp !== false) {
            const timestamp = response.data.last_modified || new Date().toISOString()
            formattedLine = `[${new Date(timestamp).toLocaleString()}] ${line}`
          }
          lines.push(formattedLine)
        }
      })
      
      lines.push('')
    })

    return lines.join('\n')
  }

  // 格式化为JSON格式
  private static formatAsJson(
    logData: Array<{ type: 'stdout' | 'stderr'; content: string; response: LogFileResponse }>,
    config: LogExportConfig
  ): string {
    const exportData = {
      metadata: {
        executionId: config.executionId,
        exportTime: new Date().toISOString(),
        format: 'json',
        includeStdout: config.includeStdout,
        includeStderr: config.includeStderr
      },
      logs: logData.map(({ type, content, response }) => ({
        type,
        filePath: response.data.file_path,
        fileSize: response.data.file_size,
        linesCount: response.data.lines_count,
        lastModified: response.data.last_modified,
        content: content.split('\n').filter(line => line.trim())
      }))
    }

    return JSON.stringify(exportData, null, 2)
  }

  // 格式化为CSV格式
  private static formatAsCsv(
    logData: Array<{ type: 'stdout' | 'stderr'; content: string; response: LogFileResponse }>,
    config: LogExportConfig
  ): string {
    const lines: string[] = []
    
    // CSV头部
    const headers = ['Type', 'Line', 'Content']
    if (config.includeTimestamp !== false) {
      headers.unshift('Timestamp')
    }
    lines.push(headers.join(','))

    logData.forEach(({ type, content, response }) => {
      const contentLines = content.split('\n')
      contentLines.forEach((line, index) => {
        if (line.trim()) {
          const row: string[] = []
          
          if (config.includeTimestamp !== false) {
            const timestamp = response.data.last_modified || new Date().toISOString()
            row.push(`"${new Date(timestamp).toLocaleString()}"`)
          }
          
          row.push(`"${type.toUpperCase()}"`)
          row.push(`"${index + 1}"`)
          row.push(`"${line.replace(/"/g, '""')}"`)
          
          lines.push(row.join(','))
        }
      })
    })

    return lines.join('\n')
  }

  // 格式化日志条目为TXT
  private static formatEntriesAsTxt(entries: LogEntry[], config?: Partial<LogExportConfig>): string {
    const lines: string[] = []
    
    lines.push(`# 日志条目导出`)
    lines.push(`# 导出时间: ${new Date().toLocaleString()}`)
    lines.push(`# 总条目数: ${entries.length}`)
    lines.push('')

    entries.forEach((entry) => {
      let line = ''
      
      if (config?.includeTimestamp !== false) {
        line += `[${new Date(entry.timestamp).toLocaleString()}] `
      }
      
      if (config?.includeLevel !== false && entry.level) {
        line += `[${entry.level}] `
      }
      
      line += `[${entry.log_type.toUpperCase()}] `
      
      if (config?.includeSource !== false && entry.source) {
        line += `[${entry.source}] `
      }
      
      line += entry.message
      
      lines.push(line)
    })

    return lines.join('\n')
  }

  // 格式化日志条目为CSV
  private static formatEntriesAsCsv(entries: LogEntry[]): string {
    const lines: string[] = []
    
    // CSV头部
    const headers = ['Timestamp', 'Level', 'Type', 'Source', 'Message']
    lines.push(headers.join(','))

    entries.forEach(entry => {
      const row = [
        `"${new Date(entry.timestamp).toLocaleString()}"`,
        `"${entry.level || ''}"`,
        `"${entry.log_type.toUpperCase()}"`,
        `"${entry.source || ''}"`,
        `"${entry.message.replace(/"/g, '""')}"`
      ]
      lines.push(row.join(','))
    })

    return lines.join('\n')
  }

  // 下载文件
  private static downloadFile(content: string, filename: string): void {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }
}

// 便捷导出函数
export const exportExecutionLogs = (executionId: string, format: ExportFormat = 'txt') => {
  return LogExporter.exportLogFile({
    executionId,
    format,
    includeStdout: true,
    includeStderr: true,
    includeTimestamp: true,
    includeLevel: true,
    includeSource: true
  })
}

export const exportLogEntries = (executionId: string, format: ExportFormat = 'json') => {
  return LogExporter.exportLogEntries(executionId, format)
}
