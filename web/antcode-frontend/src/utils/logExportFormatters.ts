import type { LogEntry, LogFileResponse } from '@/services/logs'
import type { LogExportConfig } from '@/utils/logExportTypes'

export interface RawLogData {
  type: 'stdout' | 'stderr'
  content: string
  response: LogFileResponse
}

export function formatRawLogsAsTxt(logData: RawLogData[], config: LogExportConfig): string {
  const lines = [
    '# 执行日志导出',
    `# 运行ID: ${config.runId}`,
    `# 导出时间: ${new Date().toLocaleString()}`,
    '# 格式: TXT',
    '',
  ]
  logData.forEach(({ type, content, response }) => {
    lines.push(`## ${type.toUpperCase()} 日志`)
    lines.push(`# 字节数: ${response.data.file_size}`)
    lines.push(`# 行数: ${response.data.lines_count}`, '')
    content.split('\n').forEach((line) => {
      if (!line.trim()) return
      lines.push(
        config.includeTimestamp === false ? line : `[${new Date().toLocaleString()}] ${line}`
      )
    })
    lines.push('')
  })
  return lines.join('\n')
}

export function formatRawLogsAsJson(logData: RawLogData[], config: LogExportConfig): string {
  return JSON.stringify(
    {
      metadata: {
        runId: config.runId,
        exportTime: new Date().toISOString(),
        format: 'json',
        includeStdout: config.includeStdout,
        includeStderr: config.includeStderr,
      },
      logs: logData.map(({ type, content, response }) => ({
        type,
        bytes: response.data.file_size,
        linesCount: response.data.lines_count,
        content: content.split('\n').filter((line) => line.trim()),
      })),
    },
    null,
    2
  )
}

export function formatRawLogsAsCsv(logData: RawLogData[], config: LogExportConfig): string {
  const headers = ['Type', 'Line', 'Content']
  if (config.includeTimestamp !== false) headers.unshift('Timestamp')
  const lines = [headers.join(',')]
  logData.forEach(({ type, content }) => {
    content.split('\n').forEach((line, index) => {
      if (!line.trim()) return
      const row: string[] = []
      if (config.includeTimestamp !== false) row.push(`"${new Date().toLocaleString()}"`)
      row.push(`"${type.toUpperCase()}"`, `"${index + 1}"`, `"${line.replace(/"/g, '""')}"`)
      lines.push(row.join(','))
    })
  })
  return lines.join('\n')
}

export function formatEntriesAsTxt(entries: LogEntry[], config?: Partial<LogExportConfig>): string {
  const lines = [
    '# 日志条目导出',
    `# 导出时间: ${new Date().toLocaleString()}`,
    `# 总条目数: ${entries.length}`,
    '',
  ]
  entries.forEach((entry) => {
    const parts: string[] = []
    if (config?.includeTimestamp !== false)
      parts.push(`[${new Date(entry.timestamp).toLocaleString()}]`)
    if (config?.includeLevel !== false && entry.level) parts.push(`[${entry.level}]`)
    parts.push(`[${entry.log_type.toUpperCase()}]`)
    if (config?.includeSource !== false && entry.source) parts.push(`[${entry.source}]`)
    parts.push(entry.message)
    lines.push(parts.join(' '))
  })
  return lines.join('\n')
}

export function formatEntriesAsCsv(entries: LogEntry[]): string {
  const lines = ['Timestamp,Level,Type,Source,Message']
  entries.forEach((entry) => {
    lines.push(
      [
        new Date(entry.timestamp).toLocaleString(),
        entry.level || '',
        entry.log_type.toUpperCase(),
        entry.source || '',
        entry.message,
      ]
        .map((value) => `"${value.replace(/"/g, '""')}"`)
        .join(',')
    )
  })
  return lines.join('\n')
}
