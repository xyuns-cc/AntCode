/**
 * 判据成对：正例钉「真的是 0 要显示 0」，反例钉「没拿到不能显示 0」。
 * 只有反例的话，一个恒返回 '—' 的实现也能过；只有正例的话，`?? 0` 原样也能过。
 */
import { describe, expect, it } from 'vitest'
import { NO_DATA, formatCount, formatPercent, formatRatio, summarizeRecentTasks } from './present'

describe('仪表盘的缺失值不折算成 0', () => {
  it('真的是 0 就显示 0', () => {
    expect(formatCount(0)).toBe('0')
    expect(formatRatio(0, 0)).toBe('0 / 0')
    expect(formatPercent(0)).toBe('0.0%')
  })

  it('没拿到显示占位符，不显示 0', () => {
    for (const rendered of [formatCount(null), formatCount(undefined), formatPercent(null), formatRatio(1, undefined), formatRatio(undefined, 1)]) {
      expect(rendered).toBe(NO_DATA)
      expect(rendered).not.toBe('0')
    }
  })

  it('紧凑计数保留原有的 K / M 形式', () => {
    expect(formatCount(999)).toBe('999')
    expect(formatCount(1500)).toBe('1.5K')
    expect(formatCount(2_500_000)).toBe('2.5M')
  })
})

const hour = (hour: number, success: number, failed: number) => ({
  hour,
  tasks: success + failed,
  success,
  failed,
})

describe('summarizeRecentTasks 把数值与成功率收到同一个 24 小时窗口', () => {
  it('数值与成功率同源', () => {
    const outcome = summarizeRecentTasks([hour(1, 3, 1), hour(2, 6, 0)])

    expect(outcome.success).toBe(9)
    expect(outcome.failed).toBe(1)
    expect(outcome.successRate).toBeCloseTo(90)
  })

  it('窗口内一次都没完成时成功率是 null，不是 0%', () => {
    const outcome = summarizeRecentTasks([hour(1, 0, 0)])

    expect(outcome.success).toBe(0)
    expect(outcome.successRate).toBeNull()
    expect(outcome.successRate).not.toBe(0)
  })
})
