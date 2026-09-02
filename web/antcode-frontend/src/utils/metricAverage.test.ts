/**
 * 判据成对：一条钉「必须算出正确的均值」，一条钉「必须不算出那个被稀释的错值」。
 * 只留正例的话，一个恒返回 null 的实现也能让"缺指标不进分母"这条通过。
 */
import { describe, expect, it } from 'vitest'
import { averageReportedUsage } from './metricAverage'

describe('averageReportedUsage', () => {
  it('只对上报过指标的机器求平均', () => {
    // 4 台里 1 台报了 12%，另 3 台没上报过（metrics 为 null）。
    expect(averageReportedUsage([12, undefined, undefined, undefined])).toBe(12)
  })

  it('不把缺指标的机器当成 0 计入分母', () => {
    // 旧实现按 len(workers) 除：12/4 = 3。这是本轮要杀掉的那个值。
    expect(averageReportedUsage([12, undefined, undefined, undefined])).not.toBe(3)
    expect(averageReportedUsage([80, null, null, null])).not.toBe(20)
  })

  it('真的上报了 0% 要照常进分母', () => {
    // 与「没上报过」区分开：一台真的空闲的机器必须把均值拉下来。
    expect(averageReportedUsage([12, 0])).toBe(6)
  })

  it('一台都没上报过时是 null，不是 0', () => {
    expect(averageReportedUsage([])).toBeNull()
    expect(averageReportedUsage([undefined, null])).toBeNull()
    expect(averageReportedUsage([undefined, null])).not.toBe(0)
  })
})
