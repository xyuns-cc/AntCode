import { describe, expect, it } from 'vitest'
import { DEFAULT_TYPES, LOG_TYPE_LABELS } from './enhancedLogViewerTypes'
import type { FilterState, LogMessage } from './enhancedLogViewerTypes'
import {
  calculateLogStats,
  filterLogMessages,
  historyPresentation,
  serializeLogs,
  toLogMessage,
} from './enhancedLogViewerUtils'

const log = (overrides: Partial<LogMessage> = {}): LogMessage => ({
  id: 'log-1',
  type: 'stdout',
  content: 'hello',
  timestamp: '2026-07-16T00:00:00.000Z',
  level: 'INFO',
  source: 'worker-1',
  ...overrides,
})

const filters = (overrides: Partial<FilterState> = {}): FilterState => ({
  searchText: '',
  selectedLevels: ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
  selectedTypes: [...DEFAULT_TYPES],
  ...overrides,
})

describe('enhancedLogViewerUtils', () => {
  it('在一次过滤中同时匹配内容、来源、级别和类型', () => {
    const messages = [
      log(),
      log({ id: 'log-2', type: 'stderr', level: 'ERROR', source: 'worker-2' }),
      log({ id: 'log-3', content: 'needle', level: undefined, source: undefined }),
    ]

    expect(filterLogMessages(messages, filters({ searchText: 'WORKER-2' }))).toEqual([messages[1]])
    expect(filterLogMessages(messages, filters({ searchText: 'needle', selectedLevels: ['ERROR'] })))
      .toEqual([messages[2]])
    expect(filterLogMessages(messages, filters({ selectedTypes: ['stderr'] }))).toEqual([messages[1]])
  })

  it('默认类型集合覆盖后端全部 LogType，收窄过滤器不会永久丢行', () => {
    // DEFAULT_TYPES 曾只有 stdout/stderr：system/application 在"过滤生效"时被整行丢弃，
    // 而类型选择器里没有任何能把它们选回来的选项 —— 丢掉的行不可恢复。
    expect([...DEFAULT_TYPES].sort()).toEqual(Object.keys(LOG_TYPE_LABELS).sort())

    const messages = [
      log({ id: 'log-1', type: 'stdout' }),
      log({ id: 'log-2', type: 'system' }),
      log({ id: 'log-3', type: 'application' }),
    ]

    expect(filterLogMessages(messages, filters())).toEqual(messages)
    expect(filterLogMessages(messages, filters({ selectedTypes: ['system', 'application'] })))
      .toEqual([messages[1], messages[2]])
  })

  it('单次 reduce 计算各类统计且每条错误只计一次', () => {
    expect(calculateLogStats([
      log(),
      log({ id: 'log-2', type: 'stderr', level: 'ERROR' }),
      log({ id: 'log-3', type: 'error', level: 'INFO' }),
      log({ id: 'log-4', type: 'warning', level: 'WARNING' }),
    ])).toEqual({ total: 4, stdout: 1, stderr: 1, errors: 2, warnings: 1 })
  })

  it('CSV 对所有外部字段执行公式注入防护并转义引号', () => {
    const csv = serializeLogs([log({
      timestamp: '=timestamp()',
      type: 'stdout',
      level: '+LEVEL',
      source: '  -source',
      content: '\n@cmd "quoted"',
    })], 'csv')

    expect(csv).toContain('"\'=timestamp()"')
    expect(csv).toContain('"\'+LEVEL"')
    expect(csv).toContain('"\'  -source"')
    expect(csv).toContain('"\'\n@cmd ""quoted"""')
  })

  it('历史截断状态明确展示最新行数', () => {
    expect(historyPresentation('loaded', { sentLines: 1000, truncated: true })).toEqual({
      color: 'success',
      text: '历史已截断(最新 1000)',
    })
    expect(historyPresentation('empty', { sentLines: 0, truncated: true })).toEqual({
      color: 'warning',
      text: '历史因容量限制未回放',
    })
  })

  it('转换日志时保留稳定 identity 且不复制 raw 原文', () => {
    const message = toLogMessage({
      id: 'run-1:custom:0',
      timestamp: '2026-07-16T00:00:00.000Z',
      level: 'INFO',
      log_type: 'system',
      message: 'bounded',
      sequence: 0,
    })

    expect(message.id).toBe('run-1:custom:0')
    expect(message.sequenceIdentity).toBe('run-1:custom:0')
    expect(message).not.toHaveProperty('raw')
  })
})
