import type { LogMessage } from './enhancedLogViewerTypes'
import type { LogFilter, LogFilterEvaluation, LogFilterOptions } from './logSearchTypes'

export const DEFAULT_LOG_FILTER: Readonly<LogFilter> = {
  searchText: '',
  logTypes: [],
  levels: [],
  sources: [],
  maxLines: 1000,
  caseSensitive: false,
  useRegex: false,
  showTimestamp: true,
  showLevel: true,
  showSource: true,
}

export const createLogFilter = (overrides: Partial<LogFilter> = {}): LogFilter => ({
  ...DEFAULT_LOG_FILTER,
  ...overrides,
})

export const collectFilterOptions = (messages: LogMessage[]): LogFilterOptions => {
  const types = new Set<string>()
  const levels = new Set<string>()
  const sources = new Set<string>()
  messages.forEach((item) => {
    types.add(item.type)
    if (item.level) levels.add(item.level)
    if (item.source) sources.add(item.source)
  })
  return {
    types: [...types].sort(),
    levels: [...levels].sort(),
    sources: [...sources].sort(),
  }
}

type TextMatcher = (message: LogMessage) => boolean

const searchableFields = (message: LogMessage): string[] => [
  message.content,
  message.source ?? '',
  message.level ?? '',
]

const createPlainMatcher = (searchText: string, caseSensitive: boolean): TextMatcher => {
  const search = caseSensitive ? searchText : searchText.toLowerCase()
  return (message) => searchableFields(message).some((field) => {
    const candidate = caseSensitive ? field : field.toLowerCase()
    return candidate.includes(search)
  })
}

const createTextMatcher = (filter: LogFilter): { matcher: TextMatcher; regexError?: string } => {
  const searchText = filter.searchText ?? ''
  if (!searchText) return { matcher: () => true }
  if (!filter.useRegex) return { matcher: createPlainMatcher(searchText, filter.caseSensitive === true) }
  try {
    const regex = new RegExp(searchText, filter.caseSensitive ? '' : 'i')
    return { matcher: (message) => searchableFields(message).some((field) => regex.test(field)) }
  } catch (error) {
    const details = error instanceof Error ? error.message : String(error)
    return { matcher: () => false, regexError: details }
  }
}

const matchesType = (message: LogMessage, filter: LogFilter): boolean => (
  !filter.logTypes?.length || filter.logTypes.includes(message.type)
)

const matchesLevel = (message: LogMessage, filter: LogFilter): boolean => (
  !filter.levels?.length || Boolean(message.level && filter.levels.includes(message.level))
)

const matchesSource = (message: LogMessage, filter: LogFilter): boolean => (
  !filter.sources?.length || Boolean(message.source && filter.sources.includes(message.source))
)

const matchesSelections = (message: LogMessage, filter: LogFilter): boolean => (
  matchesType(message, filter) && matchesLevel(message, filter) && matchesSource(message, filter)
)

const matchesTimeRange = (message: LogMessage, filter: LogFilter): boolean => {
  if (!filter.timeRange) return true
  const timestamp = new Date(message.timestamp).getTime()
  const [start, end] = filter.timeRange
  return timestamp >= start.valueOf() && timestamp <= end.valueOf()
}

export const applyLogFilter = (messages: LogMessage[], filter: LogFilter): LogFilterEvaluation => {
  const text = createTextMatcher(filter)
  if (text.regexError) return { messages: [], regexError: text.regexError }
  const filtered = messages.filter((item) => (
    text.matcher(item) && matchesSelections(item, filter) && matchesTimeRange(item, filter)
  ))
  const maxLines = filter.maxLines
  return {
    messages: maxLines && filtered.length > maxLines ? filtered.slice(-maxLines) : filtered,
  }
}

const TYPE_INFO: Record<string, { color: string; text: string }> = {
  stdout: { color: 'green', text: '标准输出' },
  stderr: { color: 'red', text: '标准错误' },
  error: { color: 'red', text: '错误' },
  warning: { color: 'orange', text: '警告' },
  info: { color: 'blue', text: '信息' },
  success: { color: 'green', text: '成功' },
}

export const getTypeInfo = (type: string) => TYPE_INFO[type] ?? { color: 'default', text: type.toUpperCase() }

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'default',
  INFO: 'blue',
  WARNING: 'orange',
  ERROR: 'red',
  CRITICAL: 'magenta',
}

export const getLevelColor = (level: string): string => LEVEL_COLORS[level.toUpperCase()] ?? 'default'
