import type { LogMessage } from './enhancedLogViewerTypes'
import type { LogFilter } from './logSearchTypes'

export interface VirtualLogStats {
  total: number
  filtered: number
  stdout: number
  stderr: number
  errors: number
  warnings: number
  info: number
  debug: number
}

export interface SearchFilterResult {
  filteredMessages: LogMessage[]
  filter: LogFilter
  stats: VirtualLogStats
}

export interface LogDisplayOptions {
  showTimestamp: boolean
  showLevel: boolean
  showSource: boolean
}

export interface VirtualizedListProps {
  items: LogMessage[]
  height: number
  estimatedItemHeight: number
  searchText: string
  displayOptions: LogDisplayOptions
  onMessageClick?: (message: LogMessage) => void
  /** 外部受控的自动滚动开关；未提供时使用内部状态。 */
  autoScroll?: boolean
  onAutoScrollChange?: (autoScroll: boolean) => void
}

export interface VirtualizedListRef {
  scrollToBottom: () => void
  resetVirtualization: () => void
}

export interface VirtualLogViewerProps {
  messages: LogMessage[]
  height?: number
  estimatedItemHeight?: number
  searchable?: boolean
  enableAdvancedFilter?: boolean
  filterMode?: string
  onMessageClick?: (message: LogMessage) => void
  onClear?: () => void
  autoScroll?: boolean
  onAutoScrollChange?: (autoScroll: boolean) => void
}

export interface HighlightPart {
  highlighted: boolean
  text: string
}
