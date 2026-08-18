/**
 * 走查实测：仪表盘「平均成功率」渲染出 `-500.0%`，旁边还挂着写死的「良好」。
 * 现场数据 totalRequests=2 / totalResponses=2 / totalErrors=12，旧公式
 * `(totalResponses - totalErrors) / totalResponses * 100` = -500。
 *
 * 这些用例用同一组现场数据把口径钉死：分母是请求总数、分子是 2xx/3xx 响应数，
 * 结果不可能为负；分母为 0 时是"暂无数据"而不是 0%；评价标签必须随数值变化。
 */
import { describe, it, expect } from 'vitest'
import {
  spiderSuccessRate,
  formatSuccessRate,
  successRateProgressPercent,
  successRateGrade,
  successRateView
} from './spiderSuccessRate'

const TOKEN = {
  colorSuccess: '#52c41a',
  colorWarning: '#faad14',
  colorError: '#ff4d4f',
  colorTextSecondary: '#8c8c8c'
}

describe('spiderSuccessRate', () => {
  it('走查现场数据不再算出负数', () => {
    // 2 个请求都拿到了 200；errorCount=12 来自 downloader/spider 异常计数，
    // 那是另一个总体，不能拿来减响应数。
    const rate = spiderSuccessRate({ totalRequests: 2, statusCodes: { '200': 2 } })

    expect(rate).toBe(100)
  })

  it('未收到响应的请求算作失败', () => {
    const rate = spiderSuccessRate({ totalRequests: 4, statusCodes: { '200': 1 } })

    expect(rate).toBe(25)
  })

  it('4xx/5xx 不计入成功，3xx 计入', () => {
    const rate = spiderSuccessRate({
      totalRequests: 4,
      statusCodes: { '200': 1, '301': 1, '404': 1, '500': 1 }
    })

    expect(rate).toBe(50)
  })

  it('没有任何请求时是"暂无数据"，不是 0%', () => {
    expect(spiderSuccessRate({ totalRequests: 0, statusCodes: {} })).toBeNull()
    expect(spiderSuccessRate(null)).toBeNull()
    expect(formatSuccessRate(null)).toBe('—')
    expect(successRateProgressPercent(null)).toBe(0)
    expect(successRateGrade(null).label).toBe('暂无数据')
  })

  it('评价标签跟着数值走，不是写死的"良好"', () => {
    expect(successRateGrade(99).label).toBe('良好')
    expect(successRateGrade(92).label).toBe('需关注')
    expect(successRateGrade(10).label).toBe('异常')
  })

  it('视图对象让文本 / 进度条 / 标签同源', () => {
    const view = successRateView({ totalRequests: 4, statusCodes: { '200': 1 } }, TOKEN)

    expect(view.text).toBe('25.0%')
    expect(view.percent).toBe(25)
    expect(view.label).toBe('异常')
    expect(view.color).toBe(TOKEN.colorError)
  })

  it('无数据时用中性色，不再被涂成成功绿', () => {
    const view = successRateView(null, TOKEN)

    expect(view.text).toBe('—')
    expect(view.color).toBe(TOKEN.colorTextSecondary)
  })
})
