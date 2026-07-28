import type { Dayjs } from 'dayjs'
import type { LogMessage } from './enhancedLogViewerTypes'

export interface LogFilter {
  searchText?: string
  logTypes?: string[]
  levels?: string[]
  sources?: string[]
  timeRange?: [Dayjs, Dayjs]
  maxLines?: number
  caseSensitive?: boolean
  useRegex?: boolean
  showTimestamp?: boolean
  showLevel?: boolean
  showSource?: boolean
}

export interface LogSearchFilterProps {
  messages: LogMessage[]
  onFilterChange: (filteredMessages: LogMessage[], filter: LogFilter) => void
  onFilterUpdate?: (filter: LogFilter) => void
  defaultFilter?: Partial<LogFilter>
  showAdvanced?: boolean
}

export interface LogFilterOptions {
  types: string[]
  levels: string[]
  sources: string[]
}

export interface LogFilterEvaluation {
  messages: LogMessage[]
  regexError?: string
}

export type UpdateLogFilter = (updates: Partial<LogFilter>) => void
