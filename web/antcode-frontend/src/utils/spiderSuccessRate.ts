/**
 * 爬虫成功率的统一口径。
 *
 * 旧公式是 `(totalResponses - totalErrors) / totalResponses`，两处都错：
 * - `totalErrors` 里含 downloader 异常与 spider 回调异常，这些请求**根本没有响应**，
 *   拿它去减 `totalResponses` 是两个不同总体相减，实测算出过 `-500.0%`；
 * - 没有下界，负数直接渲染进百分比与 antd `Progress`。
 *
 * 改成按定义算：**收到 2xx/3xx 响应的请求数 ÷ 全部发出的请求数**。
 * 分子取自 `statusCodes`（Scrapy `downloader/response_status_count/*`），分母取
 * `totalRequests`（`downloader/request_count`）。每个响应必然对应一个请求，因此
 * 结果天然落在 [0, 100]，不需要任何 clamp 兜底。
 *
 * 分母为 0（还没发过请求）时返回 `null` —— 那是"暂无数据"，不是"成功率 0%"。
 */

// 与后端 spider_stats_service 的域名健康度阈值保持一致
export const HEALTHY_SUCCESS_RATE = 95
export const WARNING_SUCCESS_RATE = 90
const HTTP_ERROR_STATUS_MIN = 400
const PERCENT_SCALE = 100

export interface SpiderSuccessRateInput {
  totalRequests: number
  statusCodes: Record<string, number>
}

export type SuccessRateTone = 'success' | 'warning' | 'error' | 'neutral'

export interface SuccessRateGrade {
  label: string
  tone: SuccessRateTone
}

function successfulResponses(statusCodes: Record<string, number>): number {
  return Object.entries(statusCodes).reduce((sum, [code, count]) => {
    const status = Number(code)
    return Number.isFinite(status) && status < HTTP_ERROR_STATUS_MIN ? sum + count : sum
  }, 0)
}

/** 成功率百分比；没有任何请求时返回 null。 */
export function spiderSuccessRate(stats: SpiderSuccessRateInput | null | undefined): number | null {
  if (!stats || stats.totalRequests <= 0) return null
  return (successfulResponses(stats.statusCodes ?? {}) / stats.totalRequests) * PERCENT_SCALE
}

/** 展示用文本；没有数据时给出显式占位而不是 "0%"。 */
export function formatSuccessRate(rate: number | null, fractionDigits = 1): string {
  return rate === null ? '—' : `${rate.toFixed(fractionDigits)}%`
}

/** 进度条百分比：无数据时按 0 画空条，配合中性配色。 */
export function successRateProgressPercent(rate: number | null): number {
  return rate === null ? 0 : Math.round(rate)
}

/** 评价标签必须跟着数值走，不能是写死的"良好"。 */
export function successRateGrade(rate: number | null): SuccessRateGrade {
  if (rate === null) return { label: '暂无数据', tone: 'neutral' }
  if (rate >= HEALTHY_SUCCESS_RATE) return { label: '良好', tone: 'success' }
  if (rate >= WARNING_SUCCESS_RATE) return { label: '需关注', tone: 'warning' }
  return { label: '异常', tone: 'error' }
}

export interface SuccessRateToneTokens {
  colorSuccess: string
  colorWarning: string
  colorError: string
  colorTextSecondary: string
}

/** 结构化取色，避免每个调用点各写一套 >=95 / >=80 的阈值。 */
export function successRateToneColor(tone: SuccessRateTone, token: SuccessRateToneTokens): string {
  if (tone === 'success') return token.colorSuccess
  if (tone === 'warning') return token.colorWarning
  if (tone === 'error') return token.colorError
  return token.colorTextSecondary
}

export interface SuccessRateView {
  rate: number | null
  text: string
  percent: number
  label: string
  color: string
}

/** 组件侧只取这一个视图对象，保证文本、进度条、评价标签三者永远同源。 */
export function successRateView(
  stats: SpiderSuccessRateInput | null | undefined,
  token: SuccessRateToneTokens
): SuccessRateView {
  const rate = spiderSuccessRate(stats)
  const grade = successRateGrade(rate)
  return {
    rate,
    text: formatSuccessRate(rate),
    percent: successRateProgressPercent(rate),
    label: grade.label,
    color: successRateToneColor(grade.tone, token)
  }
}
