import { describe, expect, it } from 'vitest'
import { LIMIT_PLACEHOLDER, isLimitDiverged, limitView } from './workerLimitDisplay'

describe('limitView', () => {
  it('有值时带上单位', () => {
    expect(limitView(4, '个')).toEqual({ value: '4', suffix: '个' })
  })

  it.each([null, undefined])('%s 显示占位符且不带单位', (value) => {
    expect(limitView(value, 'MB')).toEqual({ value: LIMIT_PLACEHOLDER, suffix: '' })
  })

  it('0 是真实取值而不是缺失', () => {
    expect(limitView(0, '秒')).toEqual({ value: '0', suffix: '秒' })
  })
})

describe('isLimitDiverged', () => {
  it('两边都有真值且不同才算分叉', () => {
    expect(isLimitDiverged(4, 20)).toBe(true)
  })

  it('相同不算分叉', () => {
    expect(isLimitDiverged(4, 4)).toBe(false)
  })

  it.each([
    [null, 20],
    [4, null],
    [undefined, 20],
    [4, undefined],
    [null, null]
  ])('任一侧未知就是"不知道"而不是"不一致" (%s, %s)', (effective, configured) => {
    expect(isLimitDiverged(effective, configured)).toBe(false)
  })
})
