/**
 * 集群平均使用率的唯一口径：**没有指标的机器不进分母**。
 *
 * 后端就是这么算的 —— `worker_stats_service.get_aggregate_stats` 的分母是
 * `workers_with_metrics` 而不是 `len(workers)`，`WorkerResponse.metrics` 为 null
 * 的机器（没上报过 / 该列读不回来）不参与。前端两处展示必须跟它同口径，否则同一个
 * 「平均 CPU」在 Worker 页、监控页、`/workers/stats` 三个地方各说各话。
 *
 * 一台还没上报心跳的机器不是「CPU 0%」。把它按 0 计入分母，10 台里 5 台没上报就把
 * 12% 的真实占用报成 6%：越是刚扩容、越是心跳断了的时候，这个数字报得越健康。
 *
 * 一台都没上报过时返回 null —— 那是「没有数据」，不是「平均 0%」。
 */
export const averageReportedUsage = (values: Array<number | null | undefined>): number | null => {
  const reported = values.filter((value): value is number => Number.isFinite(value))
  if (reported.length === 0) return null
  return Math.round(reported.reduce((sum, value) => sum + value, 0) / reported.length)
}
