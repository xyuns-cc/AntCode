import showNotification from '@/utils/notification'
import Logger from '@/utils/logger'
import { logService } from '@/services/logs'
import type { LogEntry, LogFileResponse } from '@/services/logs'

// LogFileResponse 是 logs.ts 里真实的响应类型
type RawLogResponse = LogFileResponse

export type ExportFormat = 'txt' | 'json' | 'csv'

export interface LogExportConfig {
  runId: string
  format: ExportFormat
  includeStdout?: boolean
  includeStderr?: boolean
  includeTimestamp?: boolean
  includeLevel?: boolean
  includeSource?: boolean
  maxLines?: number
}

export class LogExporter {
  static async exportLogFile(config: LogExportConfig): Promise<void> {
    const { runId, format, includeStdout = true, includeStderr = true } = config

    try {
      const promises: Promise<RawLogResponse>[] = []
      
      if (includeStdout) {
        promises.push(logService.getStdoutLogs(runId, config.maxLines))
      }
      
      if (includeStderr) {
        promises.push(logService.getStderrLogs(runId, config.maxLines))
      }

      const responses = await Promise.all(promises)
      const logData: Array<{ type: 'stdout' | 'stderr'; content: string; response: RawLogResponse }> = []

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
      let filename = `logs_${runId}_${new Date().toISOString().split('T')[0]}`

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

  static async exportLogEntries(runId: string, format: ExportFormat, config?: Partial<LogExportConfig>): Promise<void> {
    try {
      const response = await logService.getRunLogs(runId, {
        lines: config?.maxLines || 1000
      })

      if (!response.success || response.data.items.length === 0) {
        showNotification('warning', '没有日志条目可导出')
        return
      }

      const entries = response.data.items
      let exportContent = ''
      let filename = `log_entries_${runId}_${new Date().toISOString().split('T')[0]}`

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

  private static formatAsTxt(
    logData: Array<{ type: 'stdout' | 'stderr'; content: string; response: RawLogResponse }>,
    config: LogExportConfig
  ): string {
    const lines: string[] = []
    
    lines.push(`# 执行日志导出`)
    lines.push(`# 运行ID: ${config.runId}`)
    lines.push(`# 导出时间: ${new Date().toLocaleString()}`)
    lines.push(`# 格式: TXT`)
    lines.push('')

    logData.forEach(({ type, content, response }) => {
      lines.push(`## ${type.toUpperCase()} 日志`)
      lines.push(`# 字节数: ${response.data.file_size}`)
      lines.push(`# 行数: ${response.data.lines_count}`)
      lines.push('')
      
      const contentLines = content.split('\n')
      contentLines.forEach((line) => {
        if (line.trim()) {
          let formattedLine = line
          if (config.includeTimestamp !== false) {
            formattedLine = `[${new Date().toLocaleString()}] ${line}`
          }
          lines.push(formattedLine)
        }
      })
      
      lines.push('')
    })

    return lines.join('\n')
  }

  private static formatAsJson(
    logData: Array<{ type: 'stdout' | 'stderr'; content: string; response: RawLogResponse }>,
    config: LogExportConfig
  ): string {
    const exportData = {
      metadata: {
        runId: config.runId,
        exportTime: new Date().toISOString(),
        format: 'json',
        includeStdout: config.includeStdout,
        includeStderr: config.includeStderr
      },
      logs: logData.map(({ type, content, response }) => ({
        type,
        bytes: response.data.file_size,
        linesCount: response.data.lines_count,
        content: content.split('\n').filter(line => line.trim())
      }))
    }

    return JSON.stringify(exportData, null, 2)
  }

  private static formatAsCsv(
    logData: Array<{ type: 'stdout' | 'stderr'; content: string; response: RawLogResponse }>,
    config: LogExportConfig
  ): string {
    const lines: string[] = []
    
    const headers = ['Type', 'Line', 'Content']
    if (config.includeTimestamp !== false) {
      headers.unshift('Timestamp')
    }
    lines.push(headers.join(','))

    logData.forEach(({ type, content }) => {
      const contentLines = content.split('\n')
      contentLines.forEach((line, index) => {
        if (line.trim()) {
          const row: string[] = []
          
          if (config.includeTimestamp !== false) {
            row.push(`"${new Date().toLocaleString()}"`)
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

  private static formatEntriesAsCsv(entries: LogEntry[]): string {
    const lines: string[] = []
    
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

export const exportRunLogs = (runId: string, format: ExportFormat = 'txt') => {
  return LogExporter.exportLogFile({
    runId,
    format,
    includeStdout: true,
    includeStderr: true,
    includeTimestamp: true,
    includeLevel: true,
    includeSource: true
  })
}

export const exportLogEntries = (runId: string, format: ExportFormat = 'json') => {
  return LogExporter.exportLogEntries(runId, format)
}
