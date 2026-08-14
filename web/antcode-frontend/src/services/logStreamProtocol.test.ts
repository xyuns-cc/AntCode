// P1-SSE-01: 协议外 log_type 的容错读回归——未知类型不断流、降级为带标记
// 的 system 行，cursor 照常推进（帧通过校验才会 recordLastEventId）。
import { describe, expect, it } from 'vitest'
import { isValidStreamPayload, KNOWN_LOG_TYPES } from './logStreamProtocol'
import { toStreamLogEntry } from './logStreamMessages'

const logLinePayload = (data: Record<string, unknown>) => ({ type: 'log_line', data })

describe('isValidStreamPayload log_line 容错读', () => {
  it('已知 log_type 保持通过', () => {
    for (const logType of KNOWN_LOG_TYPES) {
      expect(isValidStreamPayload('log_line', logLinePayload({ content: 'x', log_type: logType }))).toBe(true)
    }
  })

  it('未知字符串 log_type 不视为帧损坏（如坏帧渗漏的 unspecified）', () => {
    const payload = logLinePayload({ content: 'x', log_type: 'unspecified', sequence: 3 })
    expect(isValidStreamPayload('log_line', payload)).toBe(true)
  })

  it('缺省 / null 的 log_type 仍通过', () => {
    expect(isValidStreamPayload('log_line', logLinePayload({ content: 'x' }))).toBe(true)
    expect(isValidStreamPayload('log_line', logLinePayload({ content: 'x', log_type: null }))).toBe(true)
  })

  it('非字符串 log_type 仍视为帧损坏', () => {
    expect(isValidStreamPayload('log_line', logLinePayload({ content: 'x', log_type: 7 }))).toBe(false)
    expect(isValidStreamPayload('log_line', logLinePayload({ content: 'x', log_type: {} }))).toBe(false)
  })

  it('未知 level 仍视为帧损坏（本轮只放开 log_type）', () => {
    expect(isValidStreamPayload('log_line', logLinePayload({ content: 'x', level: 'TRACE' }))).toBe(false)
  })
})

describe('toStreamLogEntry 未知类型降级', () => {
  it('未知 log_type 渲染为带原始类型标记的 system 行', () => {
    const entry = toStreamLogEntry(
      logLinePayload({ content: 'raw line', log_type: 'unspecified', sequence: 5, run_id: 'run-1' }),
      'run-1',
    )
    expect(entry).not.toBeNull()
    expect(entry!.log_type).toBe('system')
    expect(entry!.message).toBe('[未知日志类型 unspecified] raw line')
  })

  it('降级行的 id 保留原始类型，不与真实 system 序列冲突', () => {
    const degraded = toStreamLogEntry(
      logLinePayload({ content: 'a', log_type: 'unspecified', sequence: 5, run_id: 'run-1' }),
      'run-1',
    )
    const system = toStreamLogEntry(
      logLinePayload({ content: 'b', log_type: 'system', sequence: 5, run_id: 'run-1' }),
      'run-1',
    )
    expect(degraded!.id).toBe('run-1:unspecified:5')
    expect(system!.id).toBe('run-1:system:5')
    expect(degraded!.id).not.toBe(system!.id)
  })

  it('已知 log_type 不加标记、类型原样保留', () => {
    const entry = toStreamLogEntry(
      logLinePayload({ content: 'ok', log_type: 'stderr', sequence: 1, run_id: 'run-1' }),
      'run-1',
    )
    expect(entry!.log_type).toBe('stderr')
    expect(entry!.message).toBe('ok')
  })

  it('缺省 log_type 回退 stdout（既有行为不变）', () => {
    const entry = toStreamLogEntry(logLinePayload({ content: 'ok', sequence: 2 }), 'run-9')
    expect(entry!.log_type).toBe('stdout')
    expect(entry!.id).toBe('run-9:stdout:2')
  })
})

describe('isValidStreamPayload recovery_complete', () => {
  it.each([0, 1, 42])('接受非负安全整数 %s', (recoveredLines) => {
    expect(isValidStreamPayload('recovery_complete', {
      type: 'recovery_complete', recovered_lines: recoveredLines,
    })).toBe(true)
  })

  it.each([
    { type: 'recovery_complete' },
    { type: 'recovery_complete', recovered_lines: -1 },
    { type: 'recovery_complete', recovered_lines: 1.5 },
    { type: 'recovery_complete', recovered_lines: '1' },
    { type: 'recovery_complete', recovered_lines: Number.MAX_SAFE_INTEGER + 1 },
  ])('拒绝非法恢复行数 %#', (payload) => {
    expect(isValidStreamPayload('recovery_complete', payload)).toBe(false)
  })
})
