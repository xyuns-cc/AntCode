import showNotification from '@/utils/notification'
import Logger from '@/utils/logger'
import { logService } from '@/services/logs'
import type { LogFileResponse } from '@/services/logs'
import {
  formatEntriesAsCsv,
  formatEntriesAsTxt,
  formatRawLogsAsCsv,
  formatRawLogsAsJson,
  formatRawLogsAsTxt,
  type RawLogData,
} from '@/utils/logExportFormatters'
import { fetchLogEntriesForExport } from '@/utils/logExportPagination'
import type { ExportFormat, LogExportConfig } from '@/utils/logExportTypes'

export type { ExportFormat, LogExportConfig } from '@/utils/logExportTypes'

// LogFileResponse 是 logs.ts 里真实的响应类型
type RawLogResponse = LogFileResponse

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
      const logData: RawLogData[] = []

      responses.forEach((response, index) => {
        if (response.success && response.data.content) {
          const type = index === 0 && includeStdout ? 'stdout' : 'stderr'
          logData.push({
            type,
            content: response.data.content,
            response,
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
          exportContent = formatRawLogsAsTxt(logData, config)
          filename += '.txt'
          break

        case 'json':
          exportContent = formatRawLogsAsJson(logData, config)
          filename += '.json'
          break

        case 'csv':
          exportContent = formatRawLogsAsCsv(logData, config)
          filename += '.csv'
          break
      }

      this.downloadFile(exportContent, filename)
      showNotification('success', `日志已导出为 ${format.toUpperCase()} 格式`)
    } catch (error) {
      Logger.error('导出日志失败:', error)
      showNotification(
        'error',
        '导出日志失败: ' + (error instanceof Error ? error.message : String(error))
      )
    }
  }

  static async exportLogEntries(
    runId: string,
    format: ExportFormat,
    config?: Partial<LogExportConfig>
  ): Promise<void> {
    try {
      const entries = await fetchLogEntriesForExport(runId, config?.maxLines)

      if (entries.length === 0) {
        showNotification('warning', '没有日志条目可导出')
        return
      }

      let exportContent = ''
      let filename = `log_entries_${runId}_${new Date().toISOString().split('T')[0]}`

      switch (format) {
        case 'txt':
          exportContent = formatEntriesAsTxt(entries, config)
          filename += '.txt'
          break

        case 'json':
          exportContent = JSON.stringify(entries, null, 2)
          filename += '.json'
          break

        case 'csv':
          exportContent = formatEntriesAsCsv(entries)
          filename += '.csv'
          break
      }

      this.downloadFile(exportContent, filename)
      showNotification('success', `日志条目已导出为 ${format.toUpperCase()} 格式`)
    } catch (error) {
      Logger.error('导出日志条目失败:', error)
      showNotification(
        'error',
        '导出日志条目失败: ' + (error instanceof Error ? error.message : String(error))
      )
    }
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
    includeSource: true,
  })
}

export const exportLogEntries = (runId: string, format: ExportFormat = 'json') => {
  return LogExporter.exportLogEntries(runId, format)
}
