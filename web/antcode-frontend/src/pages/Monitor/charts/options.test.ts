/**
 * 图表 tooltip 的单位。
 *
 * 共用的 `tooltip` 回调无条件给数值加 `%`，那是给 CPU/内存/磁盘那几张百分比图用的。
 * 任务柱状图画的是任务**条数**，照搬过来会把 42 条任务的 tooltip 写成「任务数量: 42%」，
 * 而同一根柱子的 y 轴刻度写的是 42——同一个图里两个口径。
 *
 * 判据一正一反：任务图不许带 `%`；磁盘图必须仍带 `%`（否则"把 % 全删了"也能骗过用例）。
 */
import { describe, expect, it } from 'vitest'
import { theme } from 'antd'
import { createChartOptions, createDiskBarOptions, createTaskBarOptions } from './options'

type LabelCallback = (context: { dataset: { label?: string }; parsed: { y: number | null } }) => string

const labelOf = (options: { plugins?: { tooltip?: unknown } }, y: number | null, label?: string): string => {
  const callback = (options.plugins?.tooltip as { callbacks: { label: LabelCallback } }).callbacks.label
  return callback({ dataset: { label }, parsed: { y } })
}

const baseOptions = createChartOptions(theme.getDesignToken())

describe('图表 tooltip 单位', () => {
  it('任务柱状图报条数，不带百分号', () => {
    const options = createTaskBarOptions(baseOptions)

    expect(labelOf(options, 42, '任务数量')).toBe('任务数量: 42')
    expect(labelOf(options, 0, '任务数量')).toBe('任务数量: 0')
  })

  // 反例：百分比图必须还带 `%`，否则"到处删 %"这种改法也会让上面那条变绿。
  it('磁盘柱状图仍报百分比', () => {
    const options = createDiskBarOptions(baseOptions)

    expect(labelOf(options, 42, '磁盘使用率')).toBe('磁盘使用率: 42%')
  })

  it('两张图的 y 轴上限不同：百分比图封顶 100，条数图不封顶', () => {
    expect(createDiskBarOptions(baseOptions).scales.y).toMatchObject({ max: 100 })
    expect(createTaskBarOptions(baseOptions).scales.y).not.toHaveProperty('max')
  })
})
