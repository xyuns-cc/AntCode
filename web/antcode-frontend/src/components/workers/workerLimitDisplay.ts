/**
 * Worker 资源限额的展示视图。
 *
 * 后端对"没上报过的生效值"返回 null（见 workers_resources.py 的 _effective_limits）。
 * null 必须显示成占位符而不是数字：antd Statistic 对 undefined 走默认参数渲染成
 * "0"、对 null 走 String(null) 渲染成字面量 "null"，两种都会被当成真实限额读，
 * 而这个页面是用来做容量规划的。占位符沿用 utils/spiderSuccessRate 的全角破折号。
 */

export const LIMIT_PLACEHOLDER = '—'

export interface LimitView {
  value: string
  suffix: string
}

/** 有值才带单位；占位符后面跟单位会读成"— 个"这种半截数据。 */
export function limitView(value: number | null | undefined, suffix: string): LimitView {
  return value === null || value === undefined ? { value: LIMIT_PLACEHOLDER, suffix: '' } : { value: String(value), suffix }
}

/**
 * 控制面下发值与执行面生效值是否分叉。
 *
 * 只有两边都是真值才能下结论：任一为 null 是"不知道",不是"不一致"。
 */
export function isLimitDiverged(effective: number | null | undefined, configured: number | null | undefined): boolean {
  if (effective === null || effective === undefined) return false
  if (configured === null || configured === undefined) return false
  return effective !== configured
}
