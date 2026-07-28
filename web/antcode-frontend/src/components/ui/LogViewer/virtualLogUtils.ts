import type { LogMessage } from './enhancedLogViewerTypes'
import type { HighlightPart, LogDisplayOptions, VirtualLogStats } from './virtualLogTypes'

const VISIBLE_BUFFER_ITEMS = 3
const LARGE_LIST_CHANGE_RATIO = 0.3

export const calculateVirtualStats = (
  messages: LogMessage[],
  details: { filteredCount: number; total: number },
): VirtualLogStats => messages.reduce((stats, item) => ({
  ...stats,
  stdout: stats.stdout + Number(item.type === 'stdout'),
  stderr: stats.stderr + Number(item.type === 'stderr'),
  errors: stats.errors + Number(item.type === 'error' || item.level === 'ERROR'),
  warnings: stats.warnings + Number(item.type === 'warning' || item.level === 'WARNING'),
  info: stats.info + Number(item.type === 'info'),
  debug: stats.debug + Number(item.level === 'DEBUG'),
}), {
  total: details.total,
  filtered: details.filteredCount,
  stdout: 0,
  stderr: 0,
  errors: 0,
  warnings: 0,
  info: 0,
  debug: 0,
})

export const basicSearchLogs = (messages: LogMessage[], searchText: string): LogMessage[] => {
  if (!searchText) return messages
  const search = searchText.toLowerCase()
  return messages.filter((item) => (
    item.content.toLowerCase().includes(search)
    || Boolean(item.source?.toLowerCase().includes(search))
    || Boolean(item.level?.toLowerCase().includes(search))
  ))
}

export const displayOptionsFromFilter = (filter?: {
  showTimestamp?: boolean; showLevel?: boolean; showSource?: boolean
}): LogDisplayOptions => ({
  showTimestamp: filter?.showTimestamp !== false,
  showLevel: filter?.showLevel !== false,
  showSource: filter?.showSource !== false,
})

export const calculateItemPositions = (
  items: LogMessage[],
  details: { heights: Record<string, number>; estimatedHeight: number },
): { positions: number[]; totalHeight: number } => {
  let totalHeight = 0
  const positions = items.map((item) => {
    const position = totalHeight
    totalHeight += details.heights[item.id] || details.estimatedHeight
    return position
  })
  return { positions, totalHeight }
}

const firstVisibleIndex = (
  positions: number[],
  itemBottom: (index: number) => number,
  scrollTop: number,
): number => {
  let low = 0
  let high = positions.length - 1
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (itemBottom(middle) >= scrollTop) high = middle
    else low = middle + 1
  }
  return low
}

export const findVisibleRange = (
  positions: number[],
  details: { heights: number[]; scrollTop: number; viewportHeight: number },
): { start: number; end: number } => {
  if (positions.length === 0) return { start: 0, end: -1 }
  const itemBottom = (index: number) => positions[index] + details.heights[index]
  const first = firstVisibleIndex(positions, itemBottom, details.scrollTop)
  const last = firstVisibleIndex(positions, itemBottom, details.scrollTop + details.viewportHeight)
  return {
    start: Math.max(0, first - VISIBLE_BUFFER_ITEMS),
    end: Math.min(positions.length - 1, last + VISIBLE_BUFFER_ITEMS),
  }
}

export const positionsChanged = (previous: number[], current: number[]): boolean => {
  if (previous.length !== current.length) return true
  return current.some((position, index) => Math.abs(position - (previous[index] ?? 0)) > 0.5)
}

export const shouldResetVirtualization = (currentLength: number, previousLength: number): boolean => {
  if (currentLength === 0 || previousLength === 0) return true
  const change = Math.abs(currentLength - previousLength)
  return change > Math.max(currentLength, previousLength) * LARGE_LIST_CHANGE_RATIO
}

export const splitHighlightedText = (content: string, searchText: string): HighlightPart[] => {
  if (!searchText) return [{ highlighted: false, text: content }]
  const escaped = searchText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = content.split(new RegExp(`(${escaped})`, 'gi'))
  return parts
    .map((text, index) => ({ highlighted: index % 2 === 1, text }))
    .filter((part) => part.text.length > 0)
}
