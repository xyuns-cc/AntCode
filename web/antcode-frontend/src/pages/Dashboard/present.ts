import type { HourlyTrendItem } from '@/services/dashboard'

/**
 * 仪表盘的取值与呈现口径。
 *
 * 仪表盘的 5 个请求各自成败（`Promise.allSettled`），其中 `/dashboard/metrics` 和
 * `/workers/stats` 是**管理员专属**，普通用户稳定拿 403。过去渲染层一律 `?? 0`，于是
 * 「拿不到」和「真的是 0」长成同一个样子：普通用户看到的是「0/0 Worker 就绪、队列 0、
 * CPU 0%」——一块看起来一切正常的死板。这里的 formatter 全部把 null/undefined 渲染成
 * NO_DATA，故障不再被折算成正常数据。
 */

/** 数值缺失时的占位。「没拿到」必须和「值是 0」长得不一样。 */
export const NO_DATA = '—'

const THOUSAND = 1_000
const MILLION = 1_000_000

/** 紧凑计数；null/undefined = 这一块没拿到。 */
export const formatCount = (value: number | null | undefined): string => {
  if (value == null) return NO_DATA
  if (value >= MILLION) return `${(value / MILLION).toFixed(1)}M`
  if (value >= THOUSAND) return `${(value / THOUSAND).toFixed(1)}K`
  return value.toString()
}

/** 「x / y」形式；任一侧没拿到就整体作废，不拿 0 补另一半。 */
export const formatRatio = (part: number | null | undefined, total: number | null | undefined): string =>
  part == null || total == null ? NO_DATA : `${part} / ${total}`

export const formatPercent = (value: number | null | undefined, digits = 1): string =>
  value == null ? NO_DATA : `${value.toFixed(digits)}%`

/**
 * 「近 24 小时」的任务完成情况，唯一来源是 GET /dashboard/tasks/hourly-trend：
 * 后端按 `TaskRun.start_time` 落桶，只统计已完成（SUCCESS / FAILED）的执行记录，
 * 非管理员按自己的任务过滤 —— 与 /dashboard/summary 同一套归属口径。
 *
 * 这张卡以前拿 summary 的 `tasks.by_status.success` 当「今日完成」，那其实是
 * `Task.filter(status=SUCCESS).count()`：统计对象是**任务定义的当前状态**、范围是
 * **全时段**；而同一张卡的副标题「成功率」取的是 metrics.success_rate（**当日**、按
 * run 算）。一张卡上两个时间范围、两种统计对象。改成数值与成功率同窗口同数据源。
 */
export interface RecentTaskOutcome {
  success: number
  failed: number
  /** 窗口内一次执行都没完成时为 null —— 没有可算的样本，不是「成功率 0%」。 */
  successRate: number | null
}

export const summarizeRecentTasks = (trend: HourlyTrendItem[]): RecentTaskOutcome => {
  const success = trend.reduce((sum, item) => sum + item.success, 0)
  const failed = trend.reduce((sum, item) => sum + item.failed, 0)
  const finished = success + failed
  return { success, failed, successRate: finished === 0 ? null : (success / finished) * 100 }
}
