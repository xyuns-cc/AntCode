import { describe, expect, it } from 'vitest'
import type { LogMessage } from './enhancedLogViewerTypes'
import {
  basicSearchLogs,
  calculateItemPositions,
  calculateVirtualStats,
  displayOptionsFromFilter,
  findVisibleRange,
  shouldResetVirtualization,
  splitHighlightedText,
} from './virtualLogUtils'

const log = (id: string, overrides: Partial<LogMessage> = {}): LogMessage => ({
  id,
  type: 'stdout',
  content: `line-${id}`,
  timestamp: '2026-07-16T00:00:00.000Z',
  level: 'INFO',
  source: 'worker-1',
  ...overrides,
})

describe('virtualLogUtils', () => {
  it('按实测高度计算位置并用二分范围保留上下缓冲行', () => {
    const messages = Array.from({ length: 10 }, (_, index) => log(String(index)))
    const layout = calculateItemPositions(messages, { heights: { 2: 40 }, estimatedHeight: 20 })
    expect(layout.positions.slice(0, 5)).toEqual([0, 20, 40, 80, 100])
    expect(layout.totalHeight).toBe(220)

    const heights = messages.map((item) => item.id === '2' ? 40 : 20)
    expect(findVisibleRange(layout.positions, { heights, scrollTop: 100, viewportHeight: 40 }))
      .toEqual({ start: 0, end: 8 })
  })

  it('空列表和超出底部的滚动位置返回稳定范围', () => {
    expect(findVisibleRange([], { heights: [], scrollTop: 0, viewportHeight: 100 }))
      .toEqual({ start: 0, end: -1 })
    expect(findVisibleRange([0, 20, 40], { heights: [20, 20, 20], scrollTop: 999, viewportHeight: 100 }))
      .toEqual({ start: 0, end: 2 })
  })

  it('搜索高亮正确处理开头命中、重复命中和正则特殊字符', () => {
    expect(splitHighlightedText('foo foo.bar', 'foo')).toEqual([
      { highlighted: true, text: 'foo' },
      { highlighted: false, text: ' ' },
      { highlighted: true, text: 'foo' },
      { highlighted: false, text: '.bar' },
    ])
    expect(splitHighlightedText('a+b and a+b', 'a+b').filter((part) => part.highlighted)).toHaveLength(2)
  })

  it('基础搜索、统计和显示选项保持原有语义', () => {
    const messages = [
      log('1'),
      log('2', { type: 'stderr', level: 'ERROR', source: 'needle-worker' }),
      log('3', { type: 'warning', level: 'WARNING' }),
    ]
    expect(basicSearchLogs(messages, 'NEEDLE')).toEqual([messages[1]])
    expect(calculateVirtualStats(messages, { filteredCount: 1, total: 3 })).toEqual({
      total: 3, filtered: 1, stdout: 1, stderr: 1, errors: 1, warnings: 1, info: 0, debug: 0,
    })
    expect(displayOptionsFromFilter({ showTimestamp: false, showSource: false })).toEqual({
      showTimestamp: false, showLevel: true, showSource: false,
    })
  })

  it('仅在空列表或列表规模变化超过三成时完整重置', () => {
    expect(shouldResetVirtualization(0, 10)).toBe(true)
    expect(shouldResetVirtualization(100, 80)).toBe(false)
    expect(shouldResetVirtualization(100, 60)).toBe(true)
  })
})
