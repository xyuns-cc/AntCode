import type { LogEntry } from '@/services/logs'
import {
  DEFAULT_LEVELS,
  DEFAULT_TYPES,
  type ConnectionStatus,
  type ExportFormat,
  type FilterState,
  type HistoricalStatus,
  type LogMessage,
  type LogMessageType,
  type LogStats,
  type ViewerTheme,
} from './enhancedLogViewerTypes'

const CSV_FORMULA_PREFIX = /^[\s\uFEFF]*[=+\-@]/
let idCounter = 0

export const createUniqueId = (): string => {
  idCounter += 1
  return `${Date.now()}-${idCounter}-${Math.random().toString(36).slice(2, 11)}`
}

export const createNotice = (type: LogMessageType, content: string): LogMessage => ({
  id: createUniqueId(),
  type,
  content,
  timestamp: new Date().toISOString(),
})

export const toLogMessage = (entry: LogEntry): LogMessage => ({
  id: entry.id || createUniqueId(),
  // 无强制转换：LogMessageType 由后端 LogType 派生，赋值不成立就是编译错误。
  type: entry.log_type,
  content: entry.message,
  timestamp: entry.timestamp,
  level: entry.level,
  source: entry.source,
  sequence: entry.sequence,
  sequenceIdentity: entry.sequence === undefined ? undefined : entry.id,
})

export const isVisibleLogType = (
  type: LogMessageType,
  visibility: { showStdout: boolean; showStderr: boolean },
): boolean => {
  if (type === 'stdout') return visibility.showStdout
  if (type === 'stderr') return visibility.showStderr
  return true
}

export const filterLogMessages = (messages: LogMessage[], filters: FilterState): LogMessage[] => {
  const search = filters.searchText.toLowerCase()
  const filterLevels = filters.selectedLevels.length < DEFAULT_LEVELS.length
  const filterTypes = filters.selectedTypes.length < DEFAULT_TYPES.length
  return messages.filter((item) => {
    const searchable = `${item.content}\n${item.source ?? ''}`.toLowerCase()
    const matchesLevel = !filterLevels || !item.level || filters.selectedLevels.includes(item.level)
    const matchesType = !filterTypes || filters.selectedTypes.includes(item.type)
    return (!search || searchable.includes(search)) && matchesLevel && matchesType
  })
}

export const calculateLogStats = (messages: LogMessage[]): LogStats => messages.reduce((result, item) => ({
  total: result.total + 1,
  stdout: result.stdout + Number(item.type === 'stdout'),
  stderr: result.stderr + Number(item.type === 'stderr'),
  errors: result.errors + Number(item.type === 'error' || item.level === 'ERROR'),
  warnings: result.warnings + Number(item.type === 'warning' || item.level === 'WARNING'),
}), { total: 0, stdout: 0, stderr: 0, errors: 0, warnings: 0 })

const escapeCsvCell = (value: string): string => {
  const safeValue = CSV_FORMULA_PREFIX.test(value) ? `'${value}` : value
  return `"${safeValue.replace(/"/g, '""')}"`
}

const serializeCsv = (messages: LogMessage[]): string => {
  const header = 'Timestamp,Type,Level,Source,Content\n'
  const rows = messages.map((item) => [
    item.timestamp,
    item.type,
    item.level ?? '',
    item.source ?? '',
    item.content,
  ].map(escapeCsvCell).join(','))
  return header + rows.join('\n')
}

export const serializeLogs = (messages: LogMessage[], format: ExportFormat): string => {
  if (format === 'json') return JSON.stringify(messages, null, 2)
  if (format === 'csv') return serializeCsv(messages)
  return messages.map((item) => (
    `[${new Date(item.timestamp).toLocaleString()}] [${item.type.toUpperCase()}] ${item.content}`
  )).join('\n')
}

export const downloadLogs = (messages: LogMessage[], runId: string, format: ExportFormat): void => {
  const date = new Date().toISOString().split('T')[0]
  const blob = new Blob([serializeLogs(messages, format)], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `logs_${runId}_${date}.${format}`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export const connectionPresentation = (status: ConnectionStatus) => {
  if (status === 'connected') return { color: 'success' as const, text: '已连接' }
  if (status === 'connecting') return { color: 'processing' as const, text: '连接中' }
  if (status === 'error') return { color: 'error' as const, text: '连接错误' }
  return { color: 'default' as const, text: '未连接' }
}

export const historyPresentation = (
  status: HistoricalStatus,
  details: { sentLines: number; truncated: boolean },
) => {
  if (status === 'loading') return { color: 'processing' as const, text: '历史加载中' }
  if (status === 'empty') return details.truncated
    ? { color: 'warning' as const, text: '历史因容量限制未回放' }
    : { color: 'default' as const, text: '无历史日志' }
  if (status !== 'loaded') return { color: 'default' as const, text: '历史未加载' }
  const text = details.truncated
    ? `历史已截断(最新 ${details.sentLines})`
    : `历史已加载(${details.sentLines})`
  return { color: 'success' as const, text }
}

export const buildStatsItems = (stats: LogStats, filtered: number, token: ViewerTheme) => [
  { key: 'total', title: '总计', value: stats.total },
  { key: 'stdout', title: '正常输出', value: stats.stdout, color: token.colorSuccess },
  { key: 'stderr', title: '错误输出', value: stats.stderr, color: token.colorError },
  { key: 'errors', title: '错误', value: stats.errors, color: token.colorError },
  { key: 'warnings', title: '警告', value: stats.warnings, color: token.colorWarning },
  { key: 'filtered', title: '已过滤', value: filtered },
]
