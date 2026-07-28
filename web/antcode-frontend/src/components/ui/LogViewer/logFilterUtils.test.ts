import dayjs from 'dayjs'
import { describe, expect, it } from 'vitest'
import type { LogMessage } from './enhancedLogViewerTypes'
import { applyLogFilter, collectFilterOptions, createLogFilter } from './logFilterUtils'

const log = (id: string, overrides: Partial<LogMessage> = {}): LogMessage => ({
  id,
  type: 'stdout',
  content: `message-${id}`,
  timestamp: `2026-07-16T00:00:0${id}.000Z`,
  level: 'INFO',
  source: 'worker-1',
  ...overrides,
})

describe('logFilterUtils', () => {
  it('正则搜索不会因全局 lastIndex 在日志和字段之间漏匹配', () => {
    const messages = [
      log('1', { content: 'needle first' }),
      log('2', { content: 'other', source: 'needle-source' }),
      log('3', { content: 'needle third' }),
    ]
    const result = applyLogFilter(messages, createLogFilter({ searchText: 'needle', useRegex: true }))
    expect(result.messages).toEqual(messages)
    expect(result.regexError).toBeUndefined()
  })

  it('无效正则显式返回错误且不静默降级为普通搜索', () => {
    const result = applyLogFilter([log('1', { content: '[' })], createLogFilter({
      searchText: '[',
      useRegex: true,
    }))
    expect(result.messages).toEqual([])
    expect(result.regexError).toContain('Invalid regular expression')
  })

  it('组合类型、级别、来源、时间范围并保留最新 maxLines 条', () => {
    const messages = [
      log('1', { type: 'stderr', level: 'ERROR', source: 'worker-a' }),
      log('2', { type: 'stderr', level: 'ERROR', source: 'worker-a' }),
      log('3', { type: 'stdout', level: 'INFO', source: 'worker-b' }),
    ]
    const result = applyLogFilter(messages, createLogFilter({
      logTypes: ['stderr'],
      levels: ['ERROR'],
      sources: ['worker-a'],
      timeRange: [dayjs('2026-07-16T00:00:00Z'), dayjs('2026-07-16T00:00:09Z')],
      maxLines: 1,
    }))
    expect(result.messages).toEqual([messages[1]])
  })

  it('收集并排序实际存在的筛选选项', () => {
    expect(collectFilterOptions([
      log('1', { type: 'stderr', level: 'ERROR', source: 'worker-z' }),
      log('2', { type: 'stdout', level: 'DEBUG', source: 'worker-a' }),
    ])).toEqual({
      types: ['stderr', 'stdout'],
      levels: ['DEBUG', 'ERROR'],
      sources: ['worker-a', 'worker-z'],
    })
  })
})
